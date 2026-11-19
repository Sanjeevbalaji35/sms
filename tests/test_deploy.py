"""One-command deploy: deploy.sh is valid, and the create-admin command it uses makes a confirmed admin account,
is safe to run twice, and never takes the password on the command line. Runs with its own empty data folder."""
import os, subprocess, sys, tempfile
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

env = dict(os.environ, SMS_DATA=tempfile.mkdtemp(prefix="sms-deploy-"), DATABASE_URL="")
admin = lambda *a, pw="": subprocess.run([sys.executable, "-m", "studio.admin", *a], input=pw + "\n", env=env,
                                         capture_output=True, text=True)
check("deploy.sh is valid bash", subprocess.run(["bash", "-n", "deploy.sh"]).returncode, 0)
check("deploy.sh is runnable", os.access("deploy.sh", os.X_OK), True)
r = admin("create-admin", "Owner@Example.com", pw="correct-horse-battery")
check("create-admin makes the account", (r.returncode, "is an admin" in r.stdout), (0, True))
check("the password never appears in what it prints", "correct-horse" in r.stdout + r.stderr, False)
check("running it again is harmless", admin("create-admin", "owner@example.com", pw="whatever-else").returncode, 0)
check("it is an admin", admin("list").stdout.split(), ["admin", "owner@example.com"])
check("a short password is refused", admin("create-admin", "b@example.com", pw="short").returncode, 1)
sys.path.insert(0, ".")
os.environ.update(SMS_DATA=env["SMS_DATA"], DATABASE_URL="")
from studio import auth, db
db.init()
u = auth.login("owner@example.com", "correct-horse-battery")
check("and can sign in, already confirmed", (u.is_admin, auth.is_verified(u.id)), (True, True))
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
