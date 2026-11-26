# Workbench — claude.ai Connector (Remote MCP Server)

Chats on claude.ai (web, desktop and mobile) can record progress on the dashboard through a
**custom connector**: a small remote MCP server that exposes a handful of tools (list and show
projects, set a status, replace notes, add an item, file a suggestion). This guide covers how it
is locked down and how to set it up on the home server.

The server code is `src/workbench/mcp/`, its container is `packaging/mcp/`, and the matching
claude.ai skill comes from `workbench web-skill`.

---

## 1. Architecture

```
claude.ai (Anthropic's servers)          owner's browser (sign-in only)
          │ HTTPS                                   │ HTTPS
          ▼                                         ▼
  Cloudflare: IP allowlist rule + rate-limit rule (mcp.<domain>)
          │
          ▼  Cloudflare Tunnel (outbound-only connection from the home server)
  ┌────────────────── rootless Docker of user mcpsvc (uid 1001) ──────────────────┐
  │  cloudflared ──► workbench-web network ──► workbench-mcp :8000               │
  │                                             (GitHub login, one account)       │
  └──────────────────────────────────────────────────│────────────────────────────┘
                                                     │ Unix socket /agent/agent.sock (0600, uid 1001)
                                                     ▼
                                     dashboard server: restricted agent API
                                                     │ git commit "web(...)", push
                                                     ▼
                                               workbench-data repo
```

### 1.1 Layers

Each layer limits what the next one can be asked to do:

| Layer | What it does | What it stops |
| :--- | :--- | :--- |
| **Cloudflare WAF rule** | Lets only Anthropic's published outbound IP ranges reach the hostname, except the three browser sign-in paths | Scanners and anyone else on the internet reaching the MCP and token endpoints |
| **Cloudflare rate-limit rule** | Caps requests per IP | Brute force and floods against the sign-in pages |
| **Cloudflare Tunnel** | `cloudflared` dials out to Cloudflare; no port is opened on the router or the host | Direct connections to the home server |
| **GitHub login (OAuth)** | Every MCP request needs a token issued after a GitHub sign-in, and only the GitHub account whose numeric id is `WORKBENCH_MCP_ALLOWED_GITHUB_ID` gets one | Use by anyone but the owner, even through Anthropic's IPs |
| **Rootless container as `mcpsvc`** | Container root is host uid 1001, an unprivileged user; read-only root filesystem, all capabilities dropped, `no-new-privileges`, memory/CPU/process limits | A compromised server process gaining host privileges or filling the host |
| **Host firewall for uid 1001** | nftables drops everything `mcpsvc` sends to private address ranges and redirects its DNS to 1.1.1.1 | The container reaching the LAN, the dashboard's HTTP port, the router, or Tailscale |
| **Socket-only path to the dashboard** | The only way to the dashboard is a Unix socket file, mode 0600, owned by uid 1001 | Anything other than `mcpsvc`'s processes talking to the agent API |
| **Restricted agent API** | Reads, status and notes changes, new items and suggestions only; every input validated; ids checked against the slug pattern | Deleting, moving or renaming anything, or reaching any other dashboard route |
| **`web(...)` commits** | Every write is its own commit in `workbench-data`, labelled `web(<project>/<item>): ...` | Silent changes: every edit can be reviewed and reverted with git |

The container holds no GitHub deploy key and has no repository access: the dashboard does the
committing. The GitHub OAuth app it uses only reads the signed-in account's profile
(`read:user`) to learn its id.

### 1.2 The host firewall

The file deployed on the home server, `/etc/nftables.d/mcpsvc-egress.nft`:

```
table inet mcpsvc_egress
delete table inet mcpsvc_egress
table inet mcpsvc_egress {
  set private_v4 { type ipv4_addr; flags interval
    elements = { 10.0.0.0/8, 100.64.0.0/10, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16 } }
  set private_v6 { type ipv6_addr; flags interval; elements = { fc00::/7, fe80::/10 } }
  chain dns_redirect { type nat hook output priority dstnat; policy accept;
    meta nfproto ipv4 meta skuid 1001 udp dport 53 dnat ip to 1.1.1.1
    meta nfproto ipv4 meta skuid 1001 tcp dport 53 dnat ip to 1.1.1.1 }
  chain output { type filter hook output priority filter; policy accept;
    meta skuid != 1001 accept
    ct state established,related accept
    ip daddr @private_v4 limit rate 6/minute log prefix "mcpsvc-blocked: "
    ip daddr @private_v4 counter drop
    ip6 daddr @private_v6 limit rate 6/minute log prefix "mcpsvc-blocked: "
    ip6 daddr @private_v6 counter drop }
}
```

A oneshot systemd unit, `mcpsvc-egress.service` (`Before=user@1001.service`), loads it at boot,
so the rules are in place before `mcpsvc`'s rootless Docker starts. Rootless Docker's traffic
leaves the host as uid 1001, so the rules cover the containers too. Public destinations stay
open: the connector's GitHub calls (the OAuth code exchange at `github.com` and the account check
at `api.github.com`) and the tunnel's connection to Cloudflare are allowed. Blocked attempts
show in the kernel log with the prefix `mcpsvc-blocked:`.

The Unix socket is not network traffic, so the firewall does not affect it.

### 1.3 Sign-in flow (which URLs are hit)

The connector URL is `https://mcp.example.com/mcp`. FastMCP's OAuth proxy acts as the
authorization server claude.ai talks to, and GitHub is the identity provider behind it.

| Step | Request | From |
| :--- | :--- | :--- |
| 1 | `POST /mcp` without a token → `401` with `WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource/mcp"` | Anthropic |
| 2 | `GET /.well-known/oauth-protected-resource/mcp`, then `GET /.well-known/oauth-authorization-server` | Anthropic |
| 3 | Client identity: claude.ai's Client ID Metadata Document (advertised as supported), or `POST /register` (dynamic client registration); only claude.ai's callback URLs are accepted | Anthropic |
| 4 | `GET /authorize?...&code_challenge=...` (PKCE S256) → the connector's **consent page** `/consent` | Browser |
| 5 | Consent approved → `github.com/login/oauth/authorize` → GitHub sign-in → `GET /auth/callback` | Browser |
| 6 | The connector exchanges GitHub's code at `github.com` (server to server), then redirects the browser to `https://claude.ai/api/mcp/auth_callback` | Browser |
| 7 | `POST /token`: the connector checks the GitHub account's id; any other account gets `invalid_grant` and no token | Anthropic |
| 8 | `POST /mcp` with `Authorization: Bearer <token>` for every tool call; each request re-checks the GitHub grant and the account id (answer cached for 5 minutes) | Anthropic |

Steps 4–6 come from the owner's browser, not from Anthropic, which is why the WAF rule in
section 2.6 exempts `/authorize`, `/consent` and `/auth/callback`.

### 1.4 Tokens and restarts

- The token claude.ai holds is a JWT signed with a key derived from `WORKBENCH_MCP_SECRET_KEY`.
  It only points at a record in the connector's store; the GitHub token never leaves the server.
- The store (client registrations and the GitHub tokens, encrypted with a key also derived from
  `WORKBENCH_MCP_SECRET_KEY`) lives in the `state` volume at `/state/oauth-proxy/`.
- With the same key and volume, a restart or image update keeps everyone signed in. A new key
  signs everyone out and starts an empty store.
- GitHub OAuth app tokens do not expire and come without refresh tokens, so the connector's
  tokens last a year; revoking the grant on GitHub ends them earlier (section 3).
- The MCP endpoint is stateless (no session kept in memory), so a restart does not break a chat
  that is already connected.

### 1.5 Tools

| Tool | What it does |
| :--- | :--- |
| `list_projects` | Projects and learning paths: id, name, status; child projects show their parent |
| `show_project` | One project as a compact outline: phases or topics, then items (status, name, id, summary, notes, dependencies), plus the inbox |
| `show_learning_path` | One learning path: topics and subtopics with status, id and notes |
| `set_item_status` | A project item's status (`not_started`, `in_progress`, `done`, `parked`), and optionally its notes |
| `update_item_notes` | Replace a project item's notes and/or summary |
| `add_item` | A new item under a phase, topic or item (`parent_id`), or a new top-level group |
| `suggest_item` | A suggestion in the project's inbox, for the owner to keep or dismiss |
| `set_learning_item_status` | A learning topic's or subtopic's status (`not_started`, `in_progress`, `done`) |
| `update_learning_item_notes` | Replace a learning item's notes and/or summary |

There are no delete, move or rename tools. Names are limited to 80 characters, summaries to 300
and notes to 1000; ids must match the slug pattern. Errors from the dashboard (for example an
unknown item id) reach the model with the dashboard's own message.

---

## 2. Setup

The steps assume the public hostname `mcp.example.com`, the unprivileged user `mcpsvc`
(uid 1001 in every example here: `id -u mcpsvc` gives the real one, which then replaces 1001
everywhere, the firewall file included) with rootless Docker and the firewall from section 1.2 already in place, and an
existing `cloudflared` container in `mcpsvc`'s Docker.

### 2.1 GitHub OAuth app

On github.com, under the owner's account: **Settings > Developer settings > OAuth Apps > New
OAuth App**.

| Field | Value |
| :--- | :--- |
| Application name | `Workbench connector` (shown on GitHub's sign-in page) |
| Homepage URL | `https://mcp.example.com` |
| Authorization callback URL | `https://mcp.example.com/auth/callback` |
| Enable Device Flow | off |

Register it, copy the **Client ID**, and generate a **client secret** (shown once).

### 2.2 The allowed GitHub account id

The allowlist uses the numeric account id, not the login, because a login can be renamed and
then registered by someone else:

```bash
curl -s https://api.github.com/users/<login> | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])"
```

### 2.3 Dashboard side: the agent socket

On the home server, in the dashboard's `.env` (next to the main `docker-compose.yml`):

```bash
WORKBENCH_AGENT_DIR=/srv/workbench-agent      # host directory, mounted at /agent
WORKBENCH_AGENT_SOCKET=/agent/agent.sock      # path inside the dashboard container
WORKBENCH_AGENT_SOCKET_UID=1001               # mcpsvc: rootless container root maps to it
WORKBENCH_AGENT_SOCKET_GID=1001
```

Create the directory so that uid 1001 can reach the socket inside it without listing anything
else (every parent directory must be traversable by uid 1001, which is why a path under another
user's home usually does not work):

```bash
sudo mkdir -p /srv/workbench-agent
sudo chmod 0711 /srv/workbench-agent
docker compose up -d        # in the dashboard's directory; it creates agent.sock (0600, 1001:1001)
ls -l /srv/workbench-agent  # srw------- 1 1001 1001 ... agent.sock
```

### 2.4 The connector's `.env`

As `mcpsvc` (a full login session, so rootless Docker's environment is set, for example
`sudo machinectl shell mcpsvc@`), get the code over HTTPS and create the `.env`:

```bash
git clone https://github.com/user7217/workbench.git ~/workbench
cd ~/workbench/packaging/mcp
cp .env.example .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # → WORKBENCH_MCP_SECRET_KEY
```

| Variable | Value |
| :--- | :--- |
| `WORKBENCH_MCP_PUBLIC_URL` | `https://mcp.example.com` (scheme and host only) |
| `WORKBENCH_MCP_GITHUB_CLIENT_ID` | The OAuth app's client id |
| `WORKBENCH_MCP_GITHUB_CLIENT_SECRET` | The OAuth app's client secret |
| `WORKBENCH_MCP_ALLOWED_GITHUB_ID` | The numeric id from 2.2 |
| `WORKBENCH_MCP_SECRET_KEY` | At least 32 random characters; signs tokens and encrypts the store |
| `WORKBENCH_AGENT_DIR` | `/srv/workbench-agent` (the directory from 2.3) |
| `WORKBENCH_MCP_ALLOWED_REDIRECT_URIS` | Optional; comma-separated. Default: `https://claude.ai/api/mcp/auth_callback,https://claude.com/api/mcp/auth_callback` |

Set inside the container by `compose.yml` and not meant to change: `WORKBENCH_MCP_STATE_DIR=/state`,
`WORKBENCH_AGENT_SOCKET=/agent/agent.sock`, `WORKBENCH_MCP_HOST=0.0.0.0`, `WORKBENCH_MCP_PORT=8000`.

The server refuses to start (exit code 2, every missing or malformed setting listed in the log)
unless all of the auth settings are present and valid. It never runs without the login.

### 2.5 Build and run as `mcpsvc`

`mcpsvc` keeps its own copy of the repository: the main user's home is not readable by it.

```bash
git clone https://github.com/user7217/workbench ~/workbench   # once
docker network create workbench-web                  # once
docker network connect workbench-web <cloudflared>   # once: the existing tunnel container
cd ~/workbench/packaging/mcp
docker compose up -d --build
docker compose logs -f workbench-mcp                 # "Workbench MCP server: https://.../mcp"
```

`docker compose build` runs `docker build -f packaging/mcp/Dockerfile ..` from the repository
root; the image installs the package with its `mcp` extra from `uv.lock`. No port is
published. If Docker reports that CPU or memory limits are unsupported, the user's systemd
instance does not delegate those cgroup controllers to rootless Docker (`Delegate=cpu cpuset io
memory pids` in a drop-in for `user@.service`, see Docker's rootless documentation).

The `cloudflared` container must also get `workbench-web` back if it is recreated; adding the
network to its own compose file (as an external network) makes that permanent.

To update: `git pull && docker compose up -d --build` in the same directory. Sign-ins survive
(section 1.4).

### 2.6 Cloudflare

**Tunnel route.** In **Zero Trust > Networks > Tunnels > (the tunnel) > Public hostnames**, add
`mcp.example.com` with service type **HTTP** and URL `workbench-mcp:8000`. (For a tunnel
configured by file: an ingress rule `hostname: mcp.example.com`,
`service: http://workbench-mcp:8000`.)

**WAF custom rule** (**Security > WAF > Custom rules**), action **Block**:

```
(http.host eq "mcp.example.com"
 and not ip.src in {160.79.104.0/21 153.61.208.0/20}
 and not http.request.uri.path in {"/authorize" "/consent" "/auth/callback"})
```

The ranges are Anthropic's published **outbound** addresses, used for connector traffic, as
listed on [Anthropic's IP addresses page](https://platform.claude.com/docs/en/api/ip-addresses)
on 2026-11-26. Check that page and update the rule when it changes. The three exempt paths are
the browser's part of the sign-in (section 1.3); they only start a GitHub sign-in, and only the
allowed account ends up with a token.

**Rate-limit rule** (**Security > WAF > Rate limiting rules**): for
`http.host eq "mcp.example.com"`, block an IP that exceeds a modest rate (for example 60
requests per minute, or the closest period the Cloudflare plan offers) for 10 minutes. Normal use
is a few requests per tool call.

If **Bot Fight Mode** or a managed challenge is on for the zone, it can challenge Anthropic's
server-to-server requests; when the connector fails to connect, **Security > Events** shows
whether Cloudflare blocked or challenged the requests.

### 2.7 Add the connector in claude.ai

1. **Customize > Connectors > Add custom connector**: name `Workbench`, URL
   `https://mcp.example.com/mcp`, **Continue**.
2. Keep the detected sign-in settings (the connector supports Claude's published client identity
   and dynamic registration; no client id or secret needs entering), then **Add**.
3. **Connect**: the browser opens the connector's consent page (check that it names claude.ai's
   callback), then GitHub's sign-in for the `Workbench connector` app. Sign in with the allowed
   account. Any other GitHub account ends in a failed connection.
4. Optional: on the connector's page, set the write tools to ask for approval under **Tool
   permissions**.

### 2.8 Upload the skill

The skill tells claude.ai's model when and what to record (the smallest change, the tree built
with `parent_id`, its own ideas as suggestions, the status and privacy rules). On a machine with
Workbench installed:

```bash
workbench web-skill --out workbench-web.zip
```

Upload the zip in **Customize > Skills** and turn it on. In a chat, the connector is switched on
or off from the **+** menu.

### 2.9 Check it

```bash
curl -s https://mcp.example.com/.well-known/oauth-protected-resource/mcp   # 403 from Cloudflare: the WAF rule works
docker exec workbench-mcp python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/.well-known/oauth-authorization-server').read()[:80])"
```

Then ask a chat to list the Workbench projects. A write shows up as a `web(...)` commit in
`workbench-data` and on the dashboard.

---

## 3. Undo, revoke and rotate

| Goal | How |
| :--- | :--- |
| Undo one change | On the laptop, `workbench history <project>` lists recent commits with their SHAs; `workbench undo <sha>` reverts the `web(...)` commit in `workbench-data` and pushes. The dashboard picks it up on its next sync |
| Stop claude.ai using it | Remove the connector in claude.ai (**Customize > Connectors**, connector menu, **Remove**) |
| Revoke every token now | github.com **Settings > Applications > Authorized OAuth Apps > Workbench connector > Revoke**: the connector's account check fails within 5 minutes (the cache) and every request gets 401 |
| Sign everyone out and wipe state | Set a new `WORKBENCH_MCP_SECRET_KEY` and `docker compose up -d`; optionally `docker compose down -v` first to delete the old store |
| Rotate the GitHub client secret | Generate a new secret on the OAuth app, put it in `.env`, `docker compose up -d`, then delete the old secret on GitHub. Existing sign-ins keep working |
| Change the allowed account | Set `WORKBENCH_MCP_ALLOWED_GITHUB_ID` and restart; tokens of the previous account stop working at once (the id is checked on every request) |
| Cut the connector off the dashboard | Empty `WORKBENCH_AGENT_SOCKET` in the dashboard's `.env` and `docker compose up -d` there (no socket, no access), or `docker compose down` as `mcpsvc` |

---

## 4. Limits and failure modes

- **Dashboard down or restarting**: tools answer that the dashboard is not reachable and that
  nothing was changed. The socket directory is mounted rather than the socket file, so a
  recreated socket is picked up without restarting the connector.
- **GitHub API unavailable**: new sign-ins fail and, once the 5-minute cache runs out, tool calls
  get 401 until GitHub answers again.
- **Anthropic changes its outbound IPs**: the WAF rule blocks the connector; update the rule from
  Anthropic's page.
- **A stolen claude.ai token** (the JWT) works until the GitHub grant is revoked or the key is
  rotated, and only for the tools above; every change it makes is a revertable commit.
- **Prompt injection** in a chat can make the model call the write tools. The skill limits what
  it records, and claude.ai's per-tool approval setting can require confirmation for writes.
