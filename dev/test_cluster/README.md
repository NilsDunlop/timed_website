# TIMED local test cluster

This POSIX-only harness presents an AsyncSSH server and the small subset of
Slurm used by the connector. It is intended for macOS and Linux development;
the product connector itself remains cross-platform.

Start it from the repository root:

```bash
uv run python dev/test_cluster/server.py
```

The server listens on `127.0.0.1:2222`. Its keyboard-interactive defaults are
`dev-password` and `123456`; override them with `TEST_CLUSTER_PASSWORD` and
`TEST_CLUSTER_VERIFICATION_CODE`. Set `TEST_CLUSTER_PENDING_SECONDS` to keep
submitted jobs pending long enough to exercise queue UI.

Copy `config/profiles.example.yaml` to `~/.timed/profiles.yaml` and replace the
test-cluster data root placeholder with the absolute path to this checkout's
`test-data` directory. The harness writes only below
`dev/test_cluster/.state/`, which is gitignored. SFTP is chrooted to its test
home, direct TCP forwarding is limited to loopback destinations, and commands
run through `bash -lc` with the stand-in Slurm commands first on `PATH`.
