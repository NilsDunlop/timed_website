# TIMED imaging platform

The TIMED website lets you browse and view microscopy data where it is stored.
It reads native OME-Zarr, TIMED's live-cell collections and the packed Cell
Health dataset. For the Arrhenius NAISS cluster, a small connector on your
computer logs in over SSH, starts a private data server in a Slurm job and
shows the data in your browser.

## Getting started

You need an Arrhenius account with two-factor authentication (2FA) and
membership in the projects `naiss2026-3-679` (compute) and `naiss2024-5-630`
(storage).

### 1. Software

- [Git](https://git-scm.com/downloads)
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which also
  installs Python
- [Node.js 24](https://nodejs.org/), then run `corepack enable` once to get
  pnpm. On Windows, run it in a terminal opened as administrator.

### 2. Install and start

```bash
git clone https://github.com/NilsDunlop/timed_website.git
cd timed_website
uv sync
pnpm install --frozen-lockfile
uv run python scripts/dev.py
```

Open <http://localhost:4321> to see the website.

### 3. Connect

On the website:

1. Choose **Arrhenius (NAISS)** and a dataset, type your username and press
   **Connect**.
2. Enter your password, then the code from your 2FA app.
3. Wait for the Slurm job to start. The page shows the queue state and, once
   it says **connected**, a link to **Browse data**.

**Disconnect and cancel job** ends the job. TIMED never stores your password
or code. An SSH key is optional, and your `~/.ssh/config` entries for the host
are used.

### Your personal file

You can keep your own settings in `.timed/profiles.local.yaml` in your home
folder, on Windows, macOS or Linux. The file is never part of the repository.
For example:

```yaml
profiles:
  - id: arrhenius
    username: your-username     # fills in the Connect page
    ssh_key: ~/.ssh/id_ed25519  # offer only this key
```

Any other field of a profile can be changed the same way, such as
`slurm: {account: …}` to use another compute project. A change applies at your
next Connect; a job that is already running keeps its settings.

## Data access for the team

The shared profile, `config/profiles.yaml`, reads these paths on Arrhenius.
Members of the storage project can read them.

| Path | Used for |
| --- | --- |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/zarr` | TIMED images |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/metadata` | TIMED plate metadata |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/seg/track_v0/production/seg-track-v0` | TIMED segmentation outlines |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/biomorph/idr0080` | Cell Health images |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/scratch/qc_metrics_compute/website_export` | Cell Health QC review |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/scratch/qc_review/annotations` | QC annotations, written only by the operator |

To check a path, run `namei -l <path>` on Arrhenius. Every folder on the way
needs `x` for your account, and the last one `r` as well. If your account
can't read something, the website says so after you connect, and everything
else still works.

## Using the website

On **Data**, open a plate, pick a well and open one of its fields in the
viewer. TIMED fields have a time slider, and where segmentation exists you can
show cell and nucleus outlines under **Segmentation**. **Change dataset**
switches between TIMED and Cell Health. Every dataset is opened read-only.

See [`docs/data/METADATA.md`](docs/data/METADATA.md) for TIMED plate metadata
and [`docs/ADDING_DATASETS.md`](docs/ADDING_DATASETS.md) for adding datasets.

## If something goes wrong

- "Too many authentication failures": set `ssh_key` in your personal file.
- The login node doesn't answer: set `host: arrhenius1.hpc.arrhenius.naiss.se`
  (or `arrhenius3`) under `arrhenius` in your personal file.
- "Your account can't read …": ask for access to that path, as above.
