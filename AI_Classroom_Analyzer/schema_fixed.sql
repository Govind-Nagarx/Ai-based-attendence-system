-- =====================================================
-- AI ATTENDANCE - ADMIN / TEACHER / STUDENT SCHEMA
-- Run in Supabase SQL Editor
-- =====================================================

-- 1) Students table (only creates it if missing)
CREATE TABLE IF NOT EXISTS students (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    roll_no TEXT NOT NULL UNIQUE,
    class_name TEXT NOT NULL,
    face_registered BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2) Teachers
CREATE TABLE IF NOT EXISTS teachers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    email TEXT,
    password_hash TEXT NOT NULL,
    department TEXT,
    class_name TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3) Admins (optional DB admins; the app also supports env admin)
CREATE TABLE IF NOT EXISTS admins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4) Subjects
CREATE TABLE IF NOT EXISTS subjects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    class_name TEXT,
    teacher_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS subjects_name_class_unique
ON subjects(name, COALESCE(class_name, ''));

CREATE INDEX IF NOT EXISTS subjects_teacher_idx
ON subjects(teacher_id);

-- 5) Attendance
-- If your old attendance table has student_id BIGINT, the old column is
-- the cause of: invalid input syntax for type bigint: "UUID".
-- For a prototype/no-important-data database, use the reset block below.

CREATE TABLE IF NOT EXISTS attendance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID,
    student_name TEXT,
    roll_no TEXT NOT NULL,
    class_name TEXT,
    subject TEXT NOT NULL DEFAULT 'General',
    attendance_date DATE NOT NULL DEFAULT CURRENT_DATE,
    marked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'Present'
);

-- Add subject if an older attendance table already exists.
ALTER TABLE attendance
ADD COLUMN IF NOT EXISTS subject TEXT NOT NULL DEFAULT 'General';

ALTER TABLE attendance
ADD COLUMN IF NOT EXISTS student_name TEXT;

ALTER TABLE attendance
ADD COLUMN IF NOT EXISTS roll_no TEXT;

ALTER TABLE attendance
ADD COLUMN IF NOT EXISTS class_name TEXT;

ALTER TABLE attendance
ADD COLUMN IF NOT EXISTS attendance_date DATE DEFAULT CURRENT_DATE;

ALTER TABLE attendance
ADD COLUMN IF NOT EXISTS marked_at TIMESTAMPTZ DEFAULT now();

ALTER TABLE attendance
ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Present';

-- Remove old one-row-per-student-per-day constraint.
ALTER TABLE attendance
DROP CONSTRAINT IF EXISTS attendance_one_per_student_per_day;

DROP INDEX IF EXISTS attendance_one_per_student_per_day;

-- Subject-wise uniqueness.
CREATE UNIQUE INDEX IF NOT EXISTS attendance_student_date_subject_unique
ON attendance(roll_no, attendance_date, subject);

CREATE INDEX IF NOT EXISTS attendance_subject_date_idx
ON attendance(subject, attendance_date);

CREATE INDEX IF NOT EXISTS attendance_roll_date_idx
ON attendance(roll_no, attendance_date);

-- =====================================================
-- 6) STORAGE BUCKET
-- =====================================================
-- If this statement is rejected by your Supabase project, create a
-- private bucket named face_images manually in Storage.
INSERT INTO storage.buckets (id, name, public)
VALUES ('face_images', 'face_images', false)
ON CONFLICT (id) DO NOTHING;

-- =====================================================
-- 7) RLS
-- =====================================================
-- The Python backend should use SUPABASE_SERVICE_ROLE_KEY.
-- Service-role requests bypass RLS. Do NOT expose that key to the browser.
-- These policies are not required for the backend service-role connection.

-- =====================================================
-- 8) OPTIONAL PROTOTYPE RESET FOR BIGINT -> UUID PROBLEM
-- =====================================================
-- ONLY run this section if attendance.student_id is BIGINT and you do NOT
-- need the old attendance records. It removes/recreates attendance safely.
--
-- DROP TABLE IF EXISTS attendance CASCADE;
-- CREATE TABLE attendance (
--     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--     student_id UUID,
--     student_name TEXT,
--     roll_no TEXT NOT NULL,
--     class_name TEXT,
--     subject TEXT NOT NULL DEFAULT 'General',
--     attendance_date DATE NOT NULL DEFAULT CURRENT_DATE,
--     marked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
--     status TEXT NOT NULL DEFAULT 'Present'
-- );
-- CREATE UNIQUE INDEX attendance_student_date_subject_unique
-- ON attendance(roll_no, attendance_date, subject);

-- =====================================================
-- 9) IMPORTANT: verify your types
-- =====================================================
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name IN ('students','attendance','teachers','admins','subjects')
ORDER BY table_name, ordinal_position;
