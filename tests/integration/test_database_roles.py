"""Docker regression test; run with ALFA_TEST_DOCKER_ROLES=1 python3 this_file.py."""

import os
import subprocess
import unittest
from pathlib import Path
from uuid import uuid4


@unittest.skipUnless(os.environ.get("ALFA_TEST_DOCKER_ROLES") == "1", "opt-in Docker test")
class DatabaseRolesTest(unittest.TestCase):
    def test_password_rotation_preserves_existing_database(self):
        root = Path(__file__).resolve().parents[2]
        project = f"alfa-roles-test-{uuid4().hex}"
        environment = os.environ.copy()
        keys = {
            "ALFA_MIGRATOR_DB_PASSWORD": "old-migrator",
            "ALFA_BOT_DB_PASSWORD": "old-bot",
            "ALFA_WORKER_DB_PASSWORD": "old-worker",
        }
        environment.update(keys, POSTGRES_PASSWORD="isolated-admin")
        command = [
            "docker", "compose", "--project-name", project,
            "--env-file", "/dev/null", "-f", str(root / "compose.yaml"),
        ]

        def compose(*args, input=None, check=True):
            return subprocess.run(
                [*command, *args], cwd=root, env=environment, input=input,
                text=True, capture_output=True, check=check, timeout=120,
            )

        def admin(sql):
            return compose(
                "exec", "-T", "db", "psql", "-XAt", "-U", "postgres",
                "-d", "alfa_course", "-v", "ON_ERROR_STOP=1", input=sql,
            ).stdout.strip()

        try:
            compose("up", "-d", "--wait", "db")
            # Initialization on a fresh volume creates the roles. Record their identities
            # and some existing data before simulating new settings for an old volume.
            before = admin("SELECT oid FROM pg_roles WHERE rolname LIKE 'alfa_%' ORDER BY oid;")
            admin("CREATE TABLE public.rotation_sentinel (value text); "
                  "INSERT INTO public.rotation_sentinel VALUES ('preserved');")
            for key in keys:
                environment[key] = "new-'quoted\\password-$value-" + key
            for _ in range(2):
                compose("run", "--rm", "--no-deps", "database-roles")
                for key, role in zip(keys, ("alfa_migrator", "alfa_bot", "alfa_worker")):
                    result = compose(
                        "exec", "-T", "-e", f"PGPASSWORD={environment[key]}", "db",
                        "psql", "-XAt", "--no-password", "-h", "127.0.0.1",
                        "-U", role, "-d", "alfa_course", "-c", "SELECT current_user;",
                    )
                    self.assertEqual(result.stdout.strip(), role)
                self.assertEqual(
                    admin("SELECT value FROM public.rotation_sentinel;"), "preserved"
                )
                self.assertEqual(
                    admin("SELECT oid FROM pg_roles WHERE rolname LIKE 'alfa_%' ORDER BY oid;"),
                    before,
                )
        finally:
            # Only the UUID-named test project's containers and volumes are removed.
            compose("down", "--volumes", check=False)


if __name__ == "__main__":
    unittest.main()
