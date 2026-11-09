# TIMED imaging platform

TIMED lets you browse and view microscopy data where it is stored, without
copying it. It reads native OME-Zarr, TIMED's live-cell collections and the
packed Cell Health (idr0080) dataset. For the Arrhenius NAISS cluster, a small
connector on your computer logs in over SSH, starts a private data server in a
Slurm job and shows the data in your browser.

## Getting started

These steps are the same on Windows, macOS and Linux.

### 1. Access

Sort this out first; it takes the longest.

- **A NAISS account on Arrhenius with two-factor authentication (2FA).**
  Request it in SUPR and set your password and 2FA as NAISS describes in
  [How to get access](https://hpc.pages.naiss.se/user-documentation/support-docs/arrhenius_hpc/login/requesting_account/).
- **Membership in the compute project** `naiss2026-3-679`. TIMED submits its
  job with this project's Slurm account, `naiss2026-3-679-cpu`; without
  membership the job is refused.
- **Membership in the storage project** `naiss2024-5-630`, and read access to
  the shared data paths listed in [Data access for the team](#data-access-for-the-team).

### 2. Software

- [Git](https://git-scm.com/downloads).
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which also
  installs Python.
- [Node.js 24](https://nodejs.org/), then pnpm through corepack: run
  `corepack enable` once. On Windows, run it in a terminal opened as
  administrator.

### 3. Install and start

```bash
git clone https://github.com/NilsDunlop/timed_website.git
cd timed_website
uv sync
pnpm install --frozen-lockfile
uv run python scripts/dev.py
```

Open <http://localhost:4321>. Stop TIMED with Ctrl+C; the last command starts
it again.

### 4. Connect

1. Choose **Arrhenius (NAISS)** and a dataset, type your NAISS username and
   press **Connect**. The browser remembers the username; nothing secret is
   stored anywhere.
2. On your first connection, unless your `~/.ssh/known_hosts` already lists
   Arrhenius, TIMED shows the login node's SSH host key. Accept it only if it
   matches a fingerprint NAISS publishes for `login.hpc.arrhenius.naiss.se`
   ([source](https://hpc.pages.naiss.se/user-documentation/support-docs/arrhenius_hpc/login/login/),
   checked 2026-11-09):
   - ECDSA `SHA256:hKGSrZ0sLGKCtSx/dpH6emMKLIh+A3yFbbr6xE48VkA`
   - ED25519 `SHA256:2Nhh0rn/E3+X//GQIf2yrQdpF8yedTTU5O/awPy6qhQ`
   - RSA `SHA256:e5w/jDOuyPe3R5SaEEovBYgyGYCWZGgoH+3HWLuRk8E`

   TIMED keeps accepted keys in its own known-hosts file and never changes
   `~/.ssh`.
3. Enter your password, then the verification code from your 2FA app.

The password and the code are required at every login; an SSH key is optional.
TIMED applies your `~/.ssh/config` entries for the host, except that the
username typed on the Connect page wins over a `User` line, and it offers your
SSH agent's and default keys. To offer one key only, set `ssh_key` in your
personal file (below).

The first connection waits for the Slurm job to start; the Connect page shows
the queue state and why it waits. The first time, and after each TIMED update,
TIMED also installs its data server under `~/.timed/server` on Arrhenius, which
takes a few minutes. Once the page says **connected**, choose **Browse data**.
**Disconnect and cancel job** ends the job.

### Your personal file

The optional `~/.timed/profiles.local.yaml` (on Windows
`C:\Users\<you>\.timed\profiles.local.yaml`) is yours and never part of the
repository. An entry with a shared profile's `id` overrides that profile's
fields; any other entry adds a profile of your own. For example:

```yaml
profiles:
  - id: arrhenius
    username: your-naiss-username   # fills in the Connect page
    ssh_key: ~/.ssh/id_ed25519      # offer only this key
```

Any other field can be overridden the same way, such as `slurm: {account: …}`
for another compute project. In a Slurm profile, the metadata folder and a
collection's QC, mask and segmentation folders may contain `{username}`, which
is filled in at connect.

### Troubleshooting

- **"Your account can't read …"** appears after connecting: those datasets or
  features stay unavailable to your account. Ask the data owner for access (see
  [Data access for the team](#data-access-for-the-team)), then disconnect and
  connect again.
- **The job waits in the queue:** the Connect page shows the queue state and
  the reason. Wait, or press **Disconnect and cancel job** and try later.
- **A wrong password or code:** the login fails. Press **Reconnect** and enter
  a fresh code from your app.
- **"Too many authentication failures":** your SSH agent offered too many keys
  before the password prompt. Set `ssh_key` in your personal file.
- **The login node doesn't answer:** NAISS names
  `arrhenius1.hpc.arrhenius.naiss.se` and `arrhenius3.hpc.arrhenius.naiss.se`
  as alternatives; set one as `host` in your personal file.

## Data access for the team

The shared profile, `config/profiles.yaml`, reads these paths on Arrhenius.
This is the checklist for sharing them: the images are shared, the
segmentation masks not yet.

| Path | Used for |
| --- | --- |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/zarr` | TIMED images |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/metadata` | TIMED plate metadata |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/seg/track_v0/production/seg-track-v0` | TIMED segmentation outlines |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/biomorph/idr0080` | Cell Health images |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/scratch/qc_metrics_compute/website_export` | Cell Health QC review |
| `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/scratch/qc_review/annotations` | QC annotations, written only by the operator |

To check a path for an account, log in to Arrhenius with it and run
`namei -l <path>`. It lists every folder on the way: each needs execute (`x`)
permission for that account, and the last one read (`r`) as well. An account
that can't read a path still connects; the Connect page then lists what it
can't read. A colleague who can't write the annotations folder sees QC
read-only.

## Using TIMED

Profiles may declare ordered collections. The Connect page owns the active
**Dataset** choice and remembers it per profile; the Data page shows only that
collection and restores its last opened plate and selected well. Explicit
`collection` and `dataset` URL parameters remain deep links. Profiles without
collections keep the original filesystem browser.

Cell Health browses idr0080 by cell line, plate, well and site, with gene,
guide, control and well-type metadata. Its five stains are DNA, ER, AGP, RNA
and Mito. Color mode starts with the DNA/ER/AGP composite; Grayscale shows one
channel at a time. Cell Health keeps the selected well across plates, can
highlight the same gene or the controls, and compares up to four fields. The
optional QC page reviews a versioned QC export and records append-only
annotations. Segmentation outlines are offered where a field has masks.

The data server opens every dataset read-only and presents it to the viewer as
OME-Zarr; packed TIFF planes are decoded on demand and no converted dataset is
written. See [`docs/data/IDR0080.md`](docs/data/IDR0080.md) for the Cell Health
data, [`docs/data/METADATA.md`](docs/data/METADATA.md) for TIMED plate metadata
and [`docs/ADDING_DATASETS.md`](docs/ADDING_DATASETS.md) for adding datasets.

## Development

[`CLAUDE.md`](CLAUDE.md) is the contributor guide: architecture, commands,
tests, the local SSH/Slurm test cluster and real-data checks.
`config/profiles.dev.yaml` holds the test-cluster and fixture profiles:

```bash
uv run python scripts/make_test_data.py
uv run python scripts/dev.py --profiles config/profiles.dev.yaml
```

## Current status

- The original TIMED prototype milestones M0-M8 are complete, including local
  subprocess mode and the Arrhenius SSH/Slurm path.
- Cell Health Phase 1 (collections, packed-image browsing, metadata-aware plate
  views, Color/Grayscale, comparison and scale bars) and QC review (0.5.0) were
  accepted on Arrhenius.
- 0.5.1 validates the Berzelius P2.1 layout; 0.5.2 restructures the data server
  and viewer without changing behaviour; 0.5.3 fixes the review findings.
- 0.6.0 adds Cell Health segmentation outlines, and 0.7.0 TIMED outlines over
  time from a production segmentation folder (`segmentation_root`) or a
  presentation export (`masks_root`). 0.7.1 makes TIMED requests cheaper.
- 0.8.0 makes TIMED shareable: a shared profile without personal values, an
  optional personal file, the username on the Connect page, optional SSH keys,
  and an access report so that a path an account can't read never blocks
  connecting.

Credentials and data-server tokens are never stored, logged, committed or
shown in screenshots.
