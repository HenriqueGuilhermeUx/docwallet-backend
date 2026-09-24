import os
import sys
from collections import OrderedDict

from sqlalchemy import MetaData, create_engine, inspect, select, text


def normalize_url(value: str) -> str:
    value = (value or '').strip()
    if value.startswith('postgres://'):
        value = value.replace('postgres://', 'postgresql://', 1)
    return value


SOURCE_URL = normalize_url(os.environ.get('SOURCE_DATABASE_URL') or os.environ.get('DATABASE_URL') or '')
TARGET_URL = normalize_url(os.environ.get('TARGET_DATABASE_URL') or '')
BATCH_SIZE = int(os.environ.get('MIGRATION_BATCH_SIZE', '500'))
ALLOW_NONEMPTY_TARGET = os.environ.get('ALLOW_NONEMPTY_TARGET', 'false').lower() == 'true'


if not SOURCE_URL:
    raise SystemExit('SOURCE_DATABASE_URL/DATABASE_URL is required')
if not TARGET_URL:
    raise SystemExit('TARGET_DATABASE_URL is required')
if SOURCE_URL == TARGET_URL:
    raise SystemExit('Source and target database URLs must be different')


source = create_engine(SOURCE_URL, pool_pre_ping=True)
target = create_engine(TARGET_URL, pool_pre_ping=True)


def db_identity(engine):
    with engine.connect() as conn:
        row = conn.execute(text('select current_database(), current_user')).one()
        return {'database': row[0], 'user': row[1]}


print('SOURCE:', db_identity(source))
print('TARGET:', db_identity(target))

source_metadata = MetaData()
source_metadata.reflect(bind=source, schema='public')

if not source_metadata.tables:
    raise SystemExit('No public tables found in source database')

# Safety: target must be empty unless explicitly overridden.
target_inspector = inspect(target)
target_tables_before = target_inspector.get_table_names(schema='public')
if target_tables_before and not ALLOW_NONEMPTY_TARGET:
    with target.connect() as conn:
        nonempty = []
        for table_name in target_tables_before:
            count = conn.execute(text(f'SELECT count(*) FROM public."{table_name}"')).scalar_one()
            if count:
                nonempty.append((table_name, count))
    if nonempty:
        raise SystemExit(f'Target is not empty: {nonempty}. Set ALLOW_NONEMPTY_TARGET=true only if intentional.')

# Recreate reflected schema. SQLAlchemy reflection preserves the columns, PK/FK,
# common unique constraints and indexes required by this project.
source_metadata.create_all(bind=target, checkfirst=True)

counts = OrderedDict()

# Copy in FK-safe order. Every insert happens inside a target transaction per table.
for table in source_metadata.sorted_tables:
    table_name = table.name
    with source.connect() as src_conn:
        rows = src_conn.execute(select(table)).mappings()
        batch = []
        copied = 0
        with target.begin() as dst_conn:
            for row in rows:
                batch.append(dict(row))
                if len(batch) >= BATCH_SIZE:
                    dst_conn.execute(table.insert(), batch)
                    copied += len(batch)
                    batch.clear()
            if batch:
                dst_conn.execute(table.insert(), batch)
                copied += len(batch)
        counts[table_name] = copied
        print(f'copied {table_name}: {copied}')

# Verify row counts source vs target.
problems = []
for table_name in source_metadata.tables:
    src_table = source_metadata.tables[table_name]
    with source.connect() as src_conn, target.connect() as dst_conn:
        src_count = src_conn.execute(select(text('count(*)')).select_from(src_table)).scalar_one()
        dst_count = dst_conn.execute(text(f'SELECT count(*) FROM public."{src_table.name}"')).scalar_one()
    if src_count != dst_count:
        problems.append((src_table.name, src_count, dst_count))

if problems:
    print('COUNT MISMATCH:', problems, file=sys.stderr)
    raise SystemExit(2)

print('Migration verified successfully.')
print('Total rows copied:', sum(counts.values()))
