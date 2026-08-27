# Backend Team — Mistakes / Gaps Log

Track issues found while setting up & integrating with the backend, to report back.

## Setup / Docs

- [ ] `alembic.ini` + `alembic/` folder are at **project root**, not inside `backend/` — but doc says to `cd backend` before running `alembic upgrade head`. Contradicts own instructions.
- [ ] No `.env.example` provided — required vars (`DATABASE_URL`, `SYNC_DATABASE_URL`, `JWT_SECRET_KEY`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`) only discovered via Pydantic validation crash, not documented anywhere.
- [ ] `requirements.txt` incomplete — missing `psycopg2-binary`, `pyjwt`, `pwdlib[argon2]`. App fails to boot without manually installing these.
- [ ] No seed script for admin accounts (only `seed_cameras`, `seed_zones` exist). No way to get working login credentials out of the box — had to insert manually via SQL.
- [ ] Auth API doc doesn't document the 422 validation error response shape — `detail` is an array of `{type, loc, msg, input, ctx}` objects, not a plain string. Caused a frontend crash (React rendering object as child) until handled.

## Additional findings

- [ ] Installed Redis version should be 6.x.x or more.
- [ ] To locally test the OTP reset flow, can't preset an arbitrary OTP value — clicking "Send OTP" always generates and overwrites with a new random OTP server-side. Must read the actual value from `admins.otp_code_hash` in DB right after triggering request-otp, since there's no dev/test bypass or logging of the OTP anywhere.