# Retrieve a private sources repository

Looprd's code and images live in [pac-labs/dapi](https://github.com/pac-labs/dapi),
with the image at `ghcr.io/pac-labs/dapi`. The private repository containing your sources
list is separate. It can also contain component specifications, or reference specs in other repositories.

For GitHub, the container credential is a **fine-grained personal access token** (PAT).
It is different from a Looprd component API key, the admin token, and GHCR image-pull credentials.

## GitHub token permissions

In GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens:

1. Select `pac-labs` as resource owner and choose an expiration.
2. Select the private configuration repository **and every private repository containing a referenced spec**.
3. Grant repository **Contents: Read-only**; metadata read access is included by GitHub.
4. Complete organization approval/SSO authorization if required by the organization's policy.

A fine-grained token covers one resource owner. Referenced repositories under another organization
need a separate credential strategy; this implementation uses one GitHub credential per runtime.
Public repositories do not require additional private repository grants.
For unattended organization-wide installations, a GitHub App with Contents read permission can be a later
upgrade; automatic installation-token minting/refresh is not implemented here.

## Mount the token as a file

Create `~/.config/looprd/secrets/github-token` on the Pi using your secret manager or a local editor.
Put only the token in that file, with permissions readable by the Looprd service user:

```sh
chmod 600 ~/.config/looprd/secrets/github-token
```

Do not paste it into a spec, sources file, repository, chat, or Quadlet image definition.
The existing Quadlet already mounts `~/.config/looprd` read-only at `/etc/looprd`.
Configure `~/.config/looprd/looprd.env`:

```dotenv
LOOPRD_SOURCES=github://pac-labs/YOUR-PRIVATE-CONFIG/looprd/sources.yaml?ref=main
LOOPRD_GITHUB_TOKEN_FILE=/etc/looprd/secrets/github-token
```

Replace `YOUR-PRIVATE-CONFIG`, path, and ref with the real private configuration repository details.
No private repository name is assumed by the implementation.
After changing environment settings, restart Looprd:

```sh
systemctl --user restart looprd.service
```

The file takes precedence over the older `LOOPRD_GITHUB_TOKEN` environment value.
It is reread for GitHub requests, so replacing its contents rotates credentials without a restart.
A missing/empty token file fails the fetch rather than silently falling back to another token.

## Sources contents

The private repository could contain `looprd/sources.yaml`:

```yaml
apiVersion: looprd/v1
components:
  - id: roboscope
    repository: https://github.com/pac-labs/YOUR-ROBOSCOPE-REPO.git
    ref: main
    specification: looprd.yaml
  - id: status
    specification: status.yaml
```

The second entry loads `looprd/status.yaml` from the **same resolved configuration commit** as the sources
list. It must declare `component: status`. The first entry resolves its project ref separately,
then loads its spec at that immutable commit. Both use authenticated GitHub Contents API requests.
Files are read with the raw media type; downloaded archive URLs and transient raw download tokens are unnecessary.
You can pin refs to full commit SHAs. HTTP redirects are rejected, and GitHub credentials are sent
only to GitHub API/raw-content HTTPS hosts on their default port.

List updates take effect at the next poll. A failed login, expired token, GitHub 404/403, or bad YAML
preserves working servers. **Only removal from a successfully fetched and validated list shuts one down.**
Removing the token is not a shutdown switch. Data is kept when a component is explicitly removed.

## Where the system specification belongs

Keep the executable contract (`looprd/spec.py`), generated schemas, and implementation design in `pac-labs/dapi`
so they change with the runtime. A `ziprunner-spec` repository can hold the shared architecture/integration
document or deployment/component specs and the sources list. That choice does not affect the loader:
repository, ref, and specification path are configurable. Prefer references to versioned schema files here
over maintaining two independently edited copies of the runtime contract.

References: [GitHub repository Contents API](https://docs.github.com/en/rest/repos/contents)
and [fine-grained token setup and limitations](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).
