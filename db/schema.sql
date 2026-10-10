-- Patient registration schema (PostgreSQL / Supabase). Idempotent: safe to re-run.

do $$ begin
  create type sex_enum as enum ('Male', 'Female', 'Other', 'Decline to Answer');
exception when duplicate_object then null; end $$;

create table if not exists patients (
  patient_id              uuid primary key default gen_random_uuid(),
  first_name              varchar(50)  not null check (first_name ~ '^[[:alpha:]]+([ ''-][[:alpha:]]+)*$'),
  last_name               varchar(50)  not null check (last_name  ~ '^[[:alpha:]]+([ ''-][[:alpha:]]+)*$'),
  date_of_birth           date         not null check (date_of_birth <= current_date and date_of_birth >= date '1900-01-01'),
  sex                     sex_enum     not null,
  phone_number            char(10)     not null check (phone_number ~ '^[2-9][0-9]{2}[2-9][0-9]{6}$'),
  email                   varchar(254) check (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$'),
  address_line_1          varchar(100) not null check (length(btrim(address_line_1)) > 0),
  address_line_2          varchar(100),
  city                    varchar(100) not null check (length(btrim(city)) between 1 and 100),
  state                   char(2)      not null check (state in (
                            'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY',
                            'LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND',
                            'OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY',
                            'DC','PR','GU','VI','AS','MP')),
  zip_code                varchar(10)  not null check (zip_code ~ '^[0-9]{5}(-[0-9]{4})?$'),
  insurance_provider      varchar(100),
  insurance_member_id     varchar(40)  check (insurance_member_id ~ '^[A-Za-z0-9]+$'),
  preferred_language      varchar(30)  not null default 'English',
  emergency_contact_name  varchar(100),
  emergency_contact_phone char(10)     check (emergency_contact_phone ~ '^[2-9][0-9]{2}[2-9][0-9]{6}$'),
  created_at              timestamptz  not null default now(),   -- stored as UTC instant
  updated_at              timestamptz  not null default now(),
  deleted_at              timestamptz                             -- soft delete marker
);

-- One *active* record per phone number: this is what powers duplicate detection.
create unique index if not exists patients_active_phone_uq on patients (phone_number) where deleted_at is null;
create index if not exists patients_last_name_idx on patients (lower(last_name)) where deleted_at is null;
create index if not exists patients_dob_idx on patients (date_of_birth) where deleted_at is null;

create or replace function set_updated_at() returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end $$;

drop trigger if exists patients_set_updated_at on patients;
create trigger patients_set_updated_at before update on patients
  for each row execute function set_updated_at();

-- Bonus: transcript / summary of each call, linked to the patient it produced.
create table if not exists call_logs (
  call_id        text primary key,
  patient_id     uuid references patients(patient_id),
  caller_number  text,
  started_at     timestamptz,
  ended_at       timestamptz,
  ended_reason   text,
  summary        text,
  transcript     text,
  created_at     timestamptz not null default now()
);
create index if not exists call_logs_patient_idx on call_logs (patient_id);

-- The API connects with a server-side role; block direct access through Supabase's public REST API.
alter table patients  enable row level security;
alter table call_logs enable row level security;

-- Seed data (clearly fake). 555-01xx style numbers.
insert into patients (first_name, last_name, date_of_birth, sex, phone_number, email,
                      address_line_1, city, state, zip_code, preferred_language)
values
  ('Jane', 'Doe', '1990-04-12', 'Female', '2125550142', 'jane.doe@example.com', '123 Main Street', 'New York', 'NY', '10001', 'English'),
  ('Carlos', 'Rivera', '1985-11-03', 'Male', '3055550177', null, '45 Ocean Drive, Apt 3B', 'Miami', 'FL', '33139', 'Spanish')
on conflict do nothing;
