-- Run this ONCE in Supabase SQL Editor

ALTER TABLE students
ADD COLUMN IF NOT EXISTS email TEXT;

CREATE INDEX IF NOT EXISTS students_email_idx
ON students(email);

-- Optional: check students that still need an email
SELECT id, name, roll_no, class_name, email
FROM students
ORDER BY created_at DESC;

-- For an already registered student, you can set the email manually:
-- UPDATE students
-- SET email = 'student@gmail.com'
-- WHERE roll_no = '101';
