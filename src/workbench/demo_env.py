"""Artificial environment and demo data generator (§11)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from workbench.config import WorkbenchLocalConfig, save_local_config
from workbench.gitops import (
    clone_repo,
    commit_all,
    get_root_commit,
    init_bare_repo,
    init_repo,
    make_isolated_git_env,
    push_head_to_branch,
    run_git,
)
from workbench.yamlio import dump_yaml_roundtrip, load_yaml_str_roundtrip


def _create_project_git_repo(
    repo_dir: Path,
    github_slug: str,
    files: dict[str, str],
    extra_commits: list[tuple[str, dict[str, str]]] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Create a fake git repo with real commits and return (root_commit, all_commit_shas)."""
    init_repo(repo_dir, branch="main", env=env)
    run_git(
        ["remote", "add", "origin", f"https://github.com/{github_slug}.git"],
        cwd=repo_dir,
        env=env,
    )
    for rel, content in files.items():
        fpath = repo_dir / rel
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
    first_sha = commit_all(repo_dir, f"Initial commit for {github_slug}", env=env)
    shas = [first_sha]

    for msg, changes in extra_commits or []:
        for rel, content in changes.items():
            fpath = repo_dir / rel
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
        sha = commit_all(repo_dir, msg, env=env)
        shas.append(sha)

    root_sha = get_root_commit(repo_dir, env=env) or first_sha
    return root_sha, shas


def init_demo_environment(base_dir: Path) -> dict[str, Any]:
    """Build the complete artificial Workbench environment under base_dir (§11)."""
    base_dir = base_dir.resolve()
    remote_bare = base_dir / "remote" / "workbench-data.git"
    laptop_home = base_dir / "laptop"
    code_dir = base_dir / "code"

    env = make_isolated_git_env(laptop_home)
    env["GIT_AUTHOR_NAME"] = "Workbench Demo"
    env["GIT_AUTHOR_EMAIL"] = "demo@example.invalid"
    env["GIT_COMMITTER_NAME"] = "Workbench Demo"
    env["GIT_COMMITTER_EMAIL"] = "demo@example.invalid"

    # 1. Initialize bare workbench-data.git
    init_bare_repo(remote_bare, branch="main", env=env)

    # 2. Create fake project repos in <dir>/code/
    ws_root, ws_shas = _create_project_git_repo(
        code_dir / "weather-station",
        "demouser/weather-station",
        {"README.md": "# Weather Station\nSolar-powered sensor node.\n", "firmware/main.c": "int main() { return 0; }\n"},
        [("Add BME280 driver", {"firmware/bme280.c": "// I2C driver\n"})],
        env=env,
    )
    ra_fw_root, ra_fw_shas = _create_project_git_repo(
        code_dir / "robot-arm-firmware",
        "demouser/robot-arm-firmware",
        {"README.md": "# Robot Arm Firmware\nStepper and encoder loop.\n", "src/motor.c": "// motor loop\n"},
        [("Add closed-loop PID", {"src/pid.c": "// pid controller\n"})],
        env=env,
    )
    ra_pl_root, ra_pl_shas = _create_project_git_repo(
        code_dir / "robot-arm-planner",
        "demouser/robot-arm-planner",
        {"README.md": "# Robot Arm Planner\nInverse kinematics and trajectory planner.\n", "planner.py": "def solve_ik(): pass\n"},
        env=env,
    )
    bg_root, _ = _create_project_git_repo(
        code_dir / "budget-cli",
        "demouser/budget-cli",
        {"README.md": "# Budget CLI\nDouble-entry ledger from terminal.\n"},
        env=env,
    )
    pf_root, _ = _create_project_git_repo(
        code_dir / "portfolio-site",
        "demouser/portfolio-site",
        {"README.md": "# Portfolio Site\nStatic showcase.\n"},
        env=env,
    )
    og_root, _ = _create_project_git_repo(
        code_dir / "old-game",
        "demouser/old-game",
        {"README.md": "# Old Game\nRetro arcade experiment.\n"},
        env=env,
    )
    ns_root, _ = _create_project_git_repo(
        code_dir / "notes-sync",
        "demouser/notes-sync",
        {"README.md": "# Notes Sync\nPlaintext note synchronizer.\n"},
        env=env,
    )
    hl_root, _ = _create_project_git_repo(
        code_dir / "home-lab-cluster",
        "demouser/home-lab-cluster",
        {"README.md": "# Home Lab Cluster\nDeclarative NixOS and container stack.\n"},
        env=env,
    )
    au_root, _ = _create_project_git_repo(
        code_dir / "esp32-audio-dsp",
        "demouser/esp32-audio-dsp",
        {"README.md": "# ESP32 Audio Synth & Effects\nReal-time I2S parametric equalizer.\n"},
        env=env,
    )
    la_root, _ = _create_project_git_repo(
        code_dir / "fpga-logic-analyzer",
        "demouser/fpga-logic-analyzer",
        {"README.md": "# FPGA Logic Analyzer\nHigh-speed protocol capture over USB.\n"},
        env=env,
    )

    # Unregistered owned repo that discovery MUST register
    ht_root, _ = _create_project_git_repo(
        code_dir / "demo-habit-tracker",
        "demouser/demo-habit-tracker",
        {"README.md": "# Habit Tracker\nMinimal CLI log.\n"},
        env=env,
    )

    # Third-party clone that discovery MUST NOT register
    tp_env = dict(env)
    tp_env["GIT_AUTHOR_NAME"] = "Third Party"
    tp_env["GIT_AUTHOR_EMAIL"] = "maintainer@thirdparty.example.invalid"
    tp_env["GIT_COMMITTER_NAME"] = "Third Party"
    tp_env["GIT_COMMITTER_EMAIL"] = "maintainer@thirdparty.example.invalid"
    tp_root, _ = _create_project_git_repo(
        code_dir / "third-party-lib",
        "external-org/third-party-lib",
        {"README.md": "# Third Party Lib\nUpstream library clone.\n"},
        env=tp_env,
    )

    # 3. Seed workbench-data via a staging repo, then push to bare remote
    seed_dir = base_dir / "_seed_work"
    init_repo(seed_dir, branch="main", env=env)
    run_git(["remote", "add", "origin", str(remote_bare)], cwd=seed_dir, env=env)

    (seed_dir / "workbench.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    # Seed projects (§11 + rich architectural structures)
    projects_yaml: dict[str, str] = {
        "demo-weather-station": f"""schema_version: 1
id: demo-weather-station
name: Weather Station
description: Solar-powered environmental telemetry node logging temperature, barometric pressure, humidity, and wind vectors over long-range sub-GHz radio.
status: active
origin: agent
updated: 2026-10-20
uses_phases: true
repos:
  - github: demouser/weather-station
    root_commit: "{ws_root}"
    name: weather-station
links:
  - label: Docs
    url: https://example.com/weather-docs
  - label: Schematics
    url: https://example.com/weather-schematics
phases:
  - id: hardware
    name: Hardware & Power
    status: done
    summary: Custom four-layer carrier board, sensor breakout headers, and LiFePO4 solar harvesting.
    notes: Verified 3.3V LDO rail stability and reverse-polarity protection under cold soak.
    components:
      - id: sensor-board
        name: Sensor board
        status: done
        summary: BME280 and SHT45 wired over redundant I2C bus with TVS diodes.
        notes: Uses 0x76 primary address with hardware reset line.
        depends_on: []
        links:
          - label: Datasheet
            url: https://example.com/bme280
        refs:
          - "{ws_shas[0][:12]}"
      - id: solar-charger
        name: Solar charger
        status: done
        summary: MPPT LiFePO4 charge controller and tilted anodized panel mount.
        notes: Sustains continuous operation across seven overcast days.
        depends_on:
          - sensor-board
        links: []
        refs: []
      - id: anemometer-frontend
        name: Ultrasonic wind frontend
        status: done
        summary: Time-of-flight transducer driver and zero-crossing comparator.
        notes: Calibrated in low-speed wind tunnel.
        depends_on:
          - sensor-board
        links: []
        refs: []
  - id: firmware
    name: Embedded Firmware
    status: in_progress
    summary: Deep-sleep RTOS sampling scheduler, ring buffer storage, and LoRa packet radio telemetry.
    notes: Sleep current is down to 18 microamps with RTC wake timer enabled.
    components:
      - id: i2c-driver
        name: I2C sensor driver
        status: done
        summary: Reads temperature, pressure, and humidity frames with CRC8 verification.
        notes: Automatic bus recovery on stuck SDA line.
        depends_on:
          - sensor-board
        links: []
        refs:
          - "{ws_shas[-1][:12]}"
      - id: radio-tx
        name: Radio transmitter
        status: in_progress
        summary: Framed LoRa packet transmission every 15 minutes with ACK retries.
        notes: Working on adaptive spreading factor negotiation.
        depends_on:
          - i2c-driver
          - solar-charger
        links: []
        refs: []
      - id: flash-ringbuffer
        name: SPI NOR ring buffer
        status: done
        summary: Wear-leveled circular log storing offline telemetry samples during link outages.
        notes: ""
        depends_on:
          - i2c-driver
        links: []
        refs: []
      - id: watchdog-supervisor
        name: Hardware watchdog supervisor
        status: in_progress
        summary: Brownout detector and task heartbeat monitor.
        notes: ""
        depends_on:
          - radio-tx
        links: []
        refs: []
  - id: enclosure
    name: Weatherproof Enclosure
    status: not_started
    summary: UV-stabilized radiation shield, Gore-Tex breather vent, and mast bracket.
    notes: ""
    components:
      - id: radiation-shield
        name: Radiation shield
        status: not_started
        summary: Stacked PETG louver plates for passive solar reflection and airflow.
        notes: ""
        depends_on:
          - radio-tx
        links: []
        refs: []
      - id: mast-clamp
        name: Stainless mast mount
        status: not_started
        summary: Guy-wire collar and lightning arrestor ground lug.
        notes: ""
        depends_on:
          - radiation-shield
        links: []
        refs: []
  - id: base-receiver
    name: Base Station & Ingestion
    status: in_progress
    summary: Indoor gateway daemon decoding LoRa frames into local time-series storage.
    notes: ""
    components:
      - id: packet-decoder
        name: Binary frame decoder
        status: done
        summary: Unpacks Reed-Solomon parity and validates sensor bounds.
        notes: ""
        depends_on:
          - radio-tx
        links: []
        refs: []
      - id: timeseries-writer
        name: SQLite WAL archiver
        status: in_progress
        summary: Batches decoded readings into daily compressed partitions.
        notes: ""
        depends_on:
          - packet-decoder
        links: []
        refs: []
""",
        "demo-robot-arm": f"""schema_version: 1
id: demo-robot-arm
name: 6-DOF Robot Arm
description: Desktop robotic manipulator with closed-loop field-oriented stepper control, CAN-FD distributed joint nodes, and jerk-limited inverse kinematics planner.
status: active
origin: agent
updated: 2026-10-21
uses_phases: true
repos:
  - github: demouser/robot-arm-firmware
    root_commit: "{ra_fw_root}"
    name: robot-arm-firmware
  - github: demouser/robot-arm-planner
    root_commit: "{ra_pl_root}"
    name: robot-arm-planner
links:
  - label: CAD Models
    url: https://example.com/robot-cad
phases:
  - id: mechanics
    name: Mechanics & Drivetrain
    status: done
    summary: Cycloidal gearboxes, crossed-roller base bearings, and carbon-fiber links.
    notes: All 6 joints assembled and backlash measured under 4 arcminutes.
    components:
      - id: base-turntable
        name: Base turntable
        status: done
        summary: Crossed-roller bearing base joint with internal slip-ring channel.
        notes: ""
        depends_on: []
        links: []
        refs:
          - "{ra_fw_shas[0][:12]}"
      - id: shoulder-actuator
        name: Shoulder actuator
        status: done
        summary: Dual-disc cycloidal drive for high continuous gravity torque.
        notes: ""
        depends_on:
          - base-turntable
        links: []
        refs: []
      - id: elbow-joint
        name: Elbow joint
        status: done
        summary: Belt-driven harmonic reduction stage with zero-backlash tensioner.
        notes: ""
        depends_on:
          - shoulder-actuator
        links: []
        refs: []
      - id: wrist-differential
        name: Wrist differential
        status: done
        summary: Bevel gear 3-axis spherical wrist and pneumatic gripper mount.
        notes: ""
        depends_on:
          - elbow-joint
        links: []
        refs: []
  - id: electronics
    name: Power & Motor Electronics
    status: done
    summary: CAN-FD motor nodes, regenerative braking clamp, and magnetic encoder feedback.
    notes: ""
    components:
      - id: power-bus
        name: 24V power distribution
        status: done
        summary: Fused 24V rail with active shunt regenerative braking clamp.
        notes: ""
        depends_on:
          - base-turntable
        links: []
        refs: []
      - id: encoder-board
        name: Magnetic encoder board
        status: done
        summary: 14-bit SPI absolute angle sensor mounted on each joint shaft.
        notes: ""
        depends_on:
          - wrist-differential
          - power-bus
        links: []
        refs: []
      - id: can-driver-node
        name: CAN stepper driver node
        status: done
        summary: Field-oriented current control loop running at 20 kHz over CAN-FD.
        notes: ""
        depends_on:
          - encoder-board
        links: []
        refs:
          - "{ra_fw_shas[-1][:12]}"
      - id: estop-circuit
        name: Hardware E-Stop
        status: done
        summary: Dual-channel safety relay cutoff for motor power rail.
        notes: ""
        depends_on:
          - power-bus
        links: []
        refs: []
  - id: kinematics
    name: Kinematics & Planning
    status: in_progress
    summary: Forward and inverse kinematics solver with jerk-limited spline trajectories.
    notes: ""
    components:
      - id: dh-parameters
        name: DH parameter calibration
        status: done
        summary: Denavit-Hartenberg kinematic table calibrated via optical target.
        notes: ""
        depends_on:
          - wrist-differential
          - encoder-board
        links: []
        refs:
          - "{ra_pl_shas[0][:12]}"
      - id: ik-solver
        name: Damped least-squares IK
        status: done
        summary: Numerical Jacobian solver with Levenberg-Marquardt singularity damping.
        notes: ""
        depends_on:
          - dh-parameters
        links: []
        refs: []
      - id: trajectory-spline
        name: Quintic spline generator
        status: in_progress
        summary: Time-parameterized joint space splines with velocity and acceleration bounds.
        notes: ""
        depends_on:
          - ik-solver
          - can-driver-node
        links: []
        refs: []
      - id: collision-checker
        name: Self-collision capsules
        status: not_started
        summary: Swept capsule-capsule distance checks prior to motion execution.
        notes: ""
        depends_on:
          - ik-solver
        links: []
        refs: []
  - id: control-ui
    name: Control & Teleoperation
    status: in_progress
    summary: Real-time host daemon, Cartesian jogger, and keyframe playback.
    notes: ""
    components:
      - id: usb-can-bridge
        name: USB-CAN bridge daemon
        status: done
        summary: Streams 500 Hz synchronous joint targets from host to arm.
        notes: ""
        depends_on:
          - can-driver-node
          - estop-circuit
        links: []
        refs: []
      - id: gamepad-jogger
        name: Cartesian jog controller
        status: in_progress
        summary: 6-DOF velocity jogging in tool center point frame.
        notes: ""
        depends_on:
          - usb-can-bridge
          - trajectory-spline
        links: []
        refs: []
      - id: waypoint-playback
        name: Waypoint teaching & playback
        status: not_started
        summary: Record gravity-compensated joint keyframes and replay smooth trajectories.
        notes: ""
        depends_on:
          - gamepad-jogger
          - collision-checker
        links: []
        refs: []
suggestions:
  - id: joint-torque-logging
    name: Joint torque logging
    summary: Record per-joint current draw during playback to spot binding and wear early.
    under: control-ui
    added: 2026-10-24
  - id: gripper-end-effector
    name: Gripper end effector
    summary: Servo-driven parallel gripper with force feedback for pick-and-place demos.
    added: 2026-10-25
  - id: workspace-visualizer
    name: Workspace reach visualizer
    under: kinematics
    added: 2026-10-26
""",
        "demo-home-lab-cluster": f"""schema_version: 1
id: demo-home-lab-cluster
name: Home Server & NixOS Fleet
description: Declarative mini-PC cluster running ZFS encrypted storage, WireGuard/Tailscale mesh routing, automated restic snapshots, and local observability.
status: active
origin: agent
updated: 2026-10-21
uses_phases: true
repos:
  - github: demouser/home-lab-cluster
    root_commit: "{hl_root}"
    name: home-lab-cluster
links:
  - label: Network Topology
    url: https://example.com/homelab-topology
phases:
  - id: base-os
    name: Declarative Base OS
    status: done
    summary: Flake-based NixOS host definitions, ZFS root pools, and initrd SSH unlock.
    notes: All three nodes boot identically from locked flake inputs.
    components:
      - id: zfs-storage-pool
        name: ZFS mirrored nvme pool
        status: done
        summary: Native dataset encryption with hourly local snapshots and scrub timers.
        notes: ""
        depends_on: []
        links: []
        refs: []
      - id: initrd-unlock
        name: Headless initrd SSH unlock
        status: done
        summary: Dropbear initramfs listener for remote boot passphrase entry.
        notes: ""
        depends_on:
          - zfs-storage-pool
        links: []
        refs: []
      - id: sops-secrets
        name: SOPS age secret provisioning
        status: done
        summary: Decrypts host service keys at activation time using ed25519 host keys.
        notes: ""
        depends_on:
          - zfs-storage-pool
        links: []
        refs: []
  - id: networking
    name: Mesh Networking & Ingress
    status: done
    summary: Private overlay routing, split-horizon DNS, and internal TLS termination.
    notes: ""
    components:
      - id: tailscale-subnet
        name: Tailscale subnet router
        status: done
        summary: Advertises internal VLAN service routes with nftables ACL isolation.
        notes: ""
        depends_on:
          - sops-secrets
        links: []
        refs: []
      - id: coredns-split
        name: Split-horizon CoreDNS
        status: done
        summary: Resolves internal hostnames to overlay addresses with DNSSEC forwarding.
        notes: ""
        depends_on:
          - tailscale-subnet
        links: []
        refs: []
      - id: caddy-reverse-proxy
        name: Internal ACME reverse proxy
        status: done
        summary: DNS-01 wildcard certificates for zero-exposure local services.
        notes: ""
        depends_on:
          - coredns-split
        links: []
        refs: []
  - id: services
    name: Self-Hosted Services
    status: in_progress
    summary: Git mirror, document archive, and off-site encrypted backup replication.
    notes: ""
    components:
      - id: forgejo-mirror
        name: Private Git mirror node
        status: done
        summary: Mirrors critical repositories and hosts local CI runners.
        notes: ""
        depends_on:
          - caddy-reverse-proxy
        links: []
        refs: []
      - id: restic-offsite
        name: Offsite B2 backup job
        status: in_progress
        summary: Deduplicated nightly snapshots with automated restore verification.
        notes: Testing monthly prune retention policy.
        depends_on:
          - zfs-storage-pool
          - sops-secrets
        links: []
        refs: []
      - id: ups-nut-daemon
        name: UPS power shutdown coordinator
        status: not_started
        summary: USB HID battery monitor triggering clean ZFS export on low runtime.
        notes: ""
        depends_on:
          - zfs-storage-pool
        links: []
        refs: []
""",
        "demo-esp32-audio-dsp": f"""schema_version: 1
id: demo-esp32-audio-dsp
name: Parametric Audio DSP Engine
description: Low-latency 24-bit I2S audio processor with biquad parametric EQ cascade, rotary encoder OLED interface, and WebUSB preset control.
status: active
origin: agent
updated: 2026-10-19
uses_phases: false
repos:
  - github: demouser/esp32-audio-dsp
    root_commit: "{au_root}"
    name: esp32-audio-dsp
links: []
phases: []
topics:
  - id: audio-pipeline
    name: Real-Time I2S Pipeline
    status: done
    summary: Double-buffered DMA audio transport and fixed-point biquad cascade.
    notes: Round-trip latency measured at 2.6 milliseconds at 48 kHz sample rate.
    components:
      - id: i2s-dma-engine
        name: Ping-pong I2S DMA driver
        status: done
        summary: Zero-copy stereo 24-bit audio capture and playback over PCM5102 DAC.
        notes: ""
        depends_on: []
        links: []
        refs: []
      - id: biquad-cascade
        name: Direct-Form II biquad cascade
        status: done
        summary: Eight-band parametric peaking and shelving filters with smooth coefficient ramping.
        notes: ""
        depends_on:
          - i2s-dma-engine
        links: []
        refs: []
      - id: limiter-stage
        name: Lookahead soft-knee limiter
        status: in_progress
        summary: Prevents digital clipping after high-gain shelving boosts.
        notes: ""
        depends_on:
          - biquad-cascade
        links: []
        refs: []
  - id: panel-ui
    name: Front Panel & Presets
    status: in_progress
    summary: SPI OLED spectrum analyzer, detented encoder navigation, and NVS preset slots.
    notes: ""
    components:
      - id: oled-bode-plot
        name: Live frequency response curve
        status: done
        summary: Evaluates composite transfer function magnitude on 128x64 monochrome display.
        notes: ""
        depends_on:
          - biquad-cascade
        links: []
        refs: []
      - id: preset-storage
        name: Flash preset bank manager
        status: in_progress
        summary: Saves custom EQ curves per output headphone profile.
        notes: ""
        depends_on:
          - oled-bode-plot
        links: []
        refs: []
""",
        "demo-budget-cli": f"""schema_version: 1
id: demo-budget-cli
name: Plaintext Budget CLI
description: Double-entry plain-text accounting ledger parser, multi-currency lot tracker, and terminal cashflow reporting suite.
status: paused
origin: agent
updated: 2026-10-10
uses_phases: true
repos:
  - github: demouser/budget-cli
    root_commit: "{bg_root}"
    name: budget-cli
links: []
phases:
  - id: core-ledger
    name: Core Ledger Parser
    status: done
    summary: Parses journal files, include directives, and verifies balanced postings.
    notes: ""
    components:
      - id: lexer-parser
        name: Transaction parser
        status: done
        summary: Recursive descent parser for dated journal entries and metadata comments.
        notes: ""
        depends_on: []
        links: []
        refs: []
      - id: balance-check
        name: Multi-currency balance verifier
        status: done
        summary: Verifies zero-sum commodity lots and cost-basis conversions per transaction.
        notes: ""
        depends_on:
          - lexer-parser
        links: []
        refs: []
  - id: reports
    name: Terminal Reports
    status: parked
    summary: Monthly register and hierarchical account tree formatting.
    notes: Paused while deciding on commodity price history format.
    components:
      - id: tree-formatter
        name: Account tree view
        status: parked
        summary: Hierarchical balance sheet output with ANSI color alignment.
        notes: ""
        depends_on:
          - balance-check
        links: []
        refs: []
  - id: importers
    name: CSV Bank Importers
    status: not_started
    summary: Rule-based categorization of bank statement CSV exports into journal syntax.
    notes: ""
    components:
      - id: csv-dedup
        name: Fingerprint deduplicator
        status: not_started
        summary: Hashes date, payee, and amount to skip already-imported postings.
        notes: ""
        depends_on:
          - lexer-parser
        links: []
        refs: []
""",
        "demo-fpga-logic-analyzer": f"""schema_version: 1
id: demo-fpga-logic-analyzer
name: FPGA Protocol Logic Analyzer
description: Lattice iCE40 gateware and host protocol decoder for capturing high-speed SPI, I2C, and UART traces with RLE compression.
status: paused
origin: manual
updated: 2026-10-05
uses_phases: false
repos:
  - github: demouser/fpga-logic-analyzer
    root_commit: "{la_root}"
    name: fpga-logic-analyzer
links: []
phases: []
topics:
  - id: gateware
    name: Verilog Capture Gateware
    status: done
    summary: Synchronous input synchronizers, edge trigger comparator, and RLE FIFO.
    notes: Synthesizes cleanly at 100 MHz sample clock on iCE40HX8K.
    components:
      - id: trigger-unit
        name: Multi-channel pattern trigger
        status: done
        summary: Matches rising, falling, or level masks across 16 digital channels.
        notes: ""
        depends_on: []
        links: []
        refs: []
      - id: rle-encoder
        name: Run-length stream compressor
        status: done
        summary: Compresses idle bus periods into timestamped run counts inside BRAM FIFO.
        notes: ""
        depends_on:
          - trigger-unit
        links: []
        refs: []
  - id: host-decoder
    name: Host Stream & Protocol Decoders
    status: parked
    summary: FTDI synchronous FIFO reader and protocol frame annotations.
    notes: ""
    components:
      - id: spi-decoder
        name: SPI transaction analyzer
        status: done
        summary: Reconstructs MOSI and MISO byte streams framed by chip-select edges.
        notes: ""
        depends_on:
          - rle-encoder
        links: []
        refs: []
      - id: vcd-exporter
        name: Value Change Dump writer
        status: parked
        summary: Exports captured waveforms for inspection in GTKWave and Surfer.
        notes: ""
        depends_on:
          - spi-decoder
        links: []
        refs: []
""",
        "demo-portfolio-site": f"""schema_version: 1
id: demo-portfolio-site
name: Personal Portfolio Site
description: Minimal static engineering notebook for hardware schematics, PCB teardowns, and software architecture writeups.
status: done
origin: manual
updated: 2026-09-15
uses_phases: false
repos:
  - github: demouser/portfolio-site
    root_commit: "{pf_root}"
    name: portfolio-site
links:
  - label: Live Site
    url: https://example.com/portfolio
phases: []
topics:
  - id: design
    name: Typography & Layout
    status: done
    summary: Clean dark stylesheet, interactive SVG schematic viewer, and code blocks.
    notes: ""
    components:
      - id: article-layout
        name: Article template
        status: done
        summary: Semantic HTML layout with sidenote margin annotations.
        notes: ""
        depends_on: []
        links: []
        refs: []
      - id: schematic-pan-zoom
        name: Interactive SVG schematic embed
        status: done
        summary: Pan-and-zoom netlist highlighting for hardware project posts.
        notes: ""
        depends_on:
          - article-layout
        links: []
        refs: []
  - id: deployment
    name: Static Build Pipeline
    status: done
    summary: Automated static bundle generation and Atom feed syndication.
    notes: ""
    components:
      - id: feed-generator
        name: Atom feed builder
        status: done
        summary: Generates validated XML feed from article frontmatter.
        notes: ""
        depends_on:
          - article-layout
        links: []
        refs: []
""",
        "demo-old-game": f"""schema_version: 1
id: demo-old-game
name: Voxel Cavern Prototype
description: Early experiment in procedural 3D cave generation, cellular automata erosion, and fixed-step DDA voxel raycasting.
status: archived
origin: manual
updated: 2026-06-01
uses_phases: true
repos:
  - github: demouser/old-game
    root_commit: "{og_root}"
    name: old-game
links: []
phases:
  - id: prototype
    name: Raycast Engine
    status: done
    summary: DDA voxel traversal in C with distance fog and point-light attenuation.
    notes: Archived after exploring signed distance fields.
    components:
      - id: dda-caster
        name: DDA traversal loop
        status: done
        summary: Fixed-step octree grid raycaster.
        notes: ""
        depends_on: []
        links: []
        refs: []
""",
        "demo-notes-sync": f"""schema_version: 1
id: demo-notes-sync
name: Notes Sync
description: Conflict-free markdown folder synchronization utility using content-defined chunking.
status: active
origin: discovered
updated: 2026-10-18
uses_phases: false
repos:
  - github: demouser/notes-sync
    root_commit: "{ns_root}"
    name: notes-sync
links: []
phases: []
topics: []
""",
    }

    for pid, ytext in projects_yaml.items():
        data = load_yaml_str_roundtrip(ytext)
        dump_yaml_roundtrip(data, seed_dir / "projects" / pid / "project.yaml")

    # Seed learning paths (§11 + rich multi-topic curriculum structures)
    learning_yaml: dict[str, str] = {
        "demo-signal-processing": """schema_version: 1
id: demo-signal-processing
name: Signal Processing Basics
description: Continuous-time sampling theory, discrete Fourier analysis, FIR and IIR digital filter design, and spectral estimation.
status: active
updated: 2026-10-01
references: []
topics:
  - id: sampling
    name: Sampling & Reconstruction
    status: done
    summary: Nyquist criterion, Dirac impulse trains, and sinc interpolation.
    references: []
    subtopics:
      - id: aliasing
        name: Aliasing & Folding
        status: in_progress
        summary: Why undersampling folds high frequencies into the discrete baseband.
        references: []
      - id: quantization
        name: ADC Quantization Noise
        status: done
        summary: Signal-to-quantization-noise ratio, dithering, and oversampling.
        references: []
      - id: zero-order-hold
        name: Zero-Order Hold & DAC Droop
        status: done
        summary: Sinc-envelope frequency roll-off and digital pre-emphasis compensation.
        references: []
  - id: dft-fft
    name: Discrete Fourier Transform & FFT
    status: done
    summary: Circular convolution, spectral leakage, and Radix-2 butterfly decomposition.
    references: []
    subtopics:
      - id: window-functions
        name: Hann, Hamming & Blackman Windows
        status: done
        summary: Main-lobe width versus side-lobe attenuation tradeoffs.
        references: []
      - id: radix2-fft
        name: Decimation-in-Time Radix-2 FFT
        status: done
        summary: Bit-reversal permutation and in-place twiddle factor butterflies.
        references: []
  - id: filters
    name: Digital Filter Design
    status: in_progress
    summary: Linear-phase FIR windowing and IIR bilinear transform methods.
    references: []
    subtopics:
      - id: fir-design
        name: Windowed FIR Design
        status: in_progress
        summary: Linear-phase impulse response truncation with Kaiser windows.
        references: []
      - id: bilinear-iir
        name: Bilinear Transform & Frequency Warping
        status: not_started
        summary: Mapping analog Butterworth and Chebyshev poles into the z-plane.
        references: []
      - id: biquad-stability
        name: Fixed-Point Biquad Quantization
        status: not_started
        summary: Limit cycles, pole movement inside the unit circle, and noise shaping.
        references: []
  - id: spectral-estimation
    name: Power Spectral Density & Welch Method
    status: not_started
    summary: Periodogram variance reduction via overlapping windowed segments.
    references: []
    subtopics:
      - id: welch-averaging
        name: Overlap-Add Periodogram Averaging
        status: not_started
        summary: Variance versus spectral resolution in noisy stationary signals.
        references: []
""",
        "demo-robotics-kinematics": """schema_version: 1
id: demo-robotics-kinematics
name: Modern Robotics & Rigid-Body Kinematics
description: Screw theory, product-of-exponentials forward kinematics, manipulator Jacobians, and closed-loop dynamics.
status: active
updated: 2026-10-15
references: []
topics:
  - id: rigid-motions
    name: Configuration Space & Rigid Motions
    status: done
    summary: Rotation matrices in SO3, homogeneous transformations in SE3, and twists.
    references: []
    subtopics:
      - id: exponential-coords
        name: Rodriques Formula & Matrix Exponential
        status: done
        summary: Axis-angle representation and Lie algebra so3 skew-symmetric matrices.
        references: []
      - id: screw-axes
        name: Twists & Screw Displacements
        status: done
        summary: Chasles-Mozzi theorem and spatial velocity representations.
        references: []
  - id: poe-kinematics
    name: Product of Exponentials Formula
    status: in_progress
    summary: Space-frame and body-frame forward kinematics without link reference frame clutter.
    references: []
    subtopics:
      - id: space-poe
        name: Space-Frame PoE Derivation
        status: done
        summary: Computing end-effector pose from home configuration M and joint screw axes.
        references: []
      - id: body-jacobian
        name: Space & Body Manipulator Jacobians
        status: in_progress
        summary: Adjoint transformations, velocity ellipsoids, and kinematic singularities.
        references: []
  - id: numerical-ik
    name: Numerical Inverse Kinematics
    status: not_started
    summary: Newton-Raphson iterations on SE3 matrix logarithms and damped pseudoinverses.
    references: []
    subtopics:
      - id: se3-error-twist
        name: Body-Frame Error Twist Convergence
        status: not_started
        summary: Driving spatial configuration error to zero with joint limit clamping.
        references: []
""",
        "demo-linear-algebra": """schema_version: 1
id: demo-linear-algebra
name: Applied Linear Algebra
description: Orthogonal projections, singular value decomposition, spectral theorems, and numerical conditioning.
status: paused
updated: 2026-09-12
references: []
topics:
  - id: subspaces-orthogonality
    name: Four Fundamental Subspaces & QR
    status: done
    summary: Column space, nullspace, Gram-Schmidt orthogonalization, and Householder reflections.
    references: []
    subtopics:
      - id: householder-qr
        name: Householder Reflections for QR
        status: done
        summary: Numerically stable triangularization without loss of orthogonality.
        references: []
      - id: normal-equations
        name: Orthogonal Projection & Least Squares
        status: done
        summary: Projecting incompatible measurement vectors onto the column space.
        references: []
  - id: svd
    name: Singular Value Decomposition
    status: in_progress
    summary: Geometric interpretation of orthogonal input-output bases and rank-k approximations.
    references: []
    subtopics:
      - id: pseudoinverse
        name: Moore-Penrose Pseudoinverse
        status: in_progress
        summary: Minimum-norm least squares solutions for rank-deficient and ill-conditioned systems.
        references: []
      - id: eckart-young
        name: Low-Rank Matrix Approximation
        status: done
        summary: Optimal spectral and Frobenius norm compression via truncated singular modes.
        references: []
  - id: eigen-spectral
    name: Spectral Theorem & Positive Definite Matrices
    status: not_started
    summary: Cholesky factorization, Rayleigh quotients, and quadratic forms.
    references: []
    subtopics:
      - id: cholesky-spd
        name: Cholesky Decomposition & Conditioning
        status: not_started
        summary: Symmetric positive-definite solvers and ellipse principal axes.
        references: []
""",
        "demo-control-theory": """schema_version: 1
id: demo-control-theory
name: Classical & State-Space Control
description: Frequency-domain loop shaping, Nyquist stability margins, state observers, and linear quadratic regulators.
status: done
updated: 2026-08-20
references: []
topics:
  - id: pid-stability
    name: PID & Frequency-Domain Loop Shaping
    status: done
    summary: Lead-lag compensators, sensitivity transfer functions, and integrator anti-windup.
    references: []
    subtopics:
      - id: bode-plots
        name: Bode & Nyquist Stability Criterion
        status: done
        summary: Gain margin, phase margin, and encirclements of the critical point.
        references: []
      - id: anti-windup
        name: Actuator Saturation & Back-Calculation
        status: done
        summary: Discharging integrator state during torque or voltage clamping.
        references: []
  - id: state-space-lqr
    name: Controllability, LQR & Luenberger Observers
    status: done
    summary: Algebraic Riccati equation, pole placement, and separation principle.
    references: []
    subtopics:
      - id: riccati-lqr
        name: Infinite-Horizon LQR Cost Tradeoffs
        status: done
        summary: Balancing state excursion penalty Q against control effort R.
        references: []
      - id: kalman-duality
        name: Observer Duality & Kalman Gain
        status: done
        summary: Optimal state estimation under process and measurement covariance.
        references: []
""",
    }

    for lid, ytext in learning_yaml.items():
        data = load_yaml_str_roundtrip(ytext)
        dump_yaml_roundtrip(data, seed_dir / "learning" / lid / "path.yaml")

    # Topic markdown files for demo-signal-processing and demo-robotics-kinematics
    dsp_topics_dir = seed_dir / "learning" / "demo-signal-processing" / "topics"
    dsp_topics_dir.mkdir(parents=True, exist_ok=True)
    (dsp_topics_dir / "sampling.md").write_text(
        "# Sampling & Reconstruction\n\n"
        "A bandlimited continuous-time signal $x(t)$ with maximum frequency $f_{\\max}$ "
        "can be reconstructed exactly from uniform samples taken at $f_s > 2 f_{\\max}$.\n\n"
        "## Core Principles\n\n"
        "- Sampling in time corresponds to periodic replication of the spectrum in frequency.\n"
        "- Ideal reconstruction applies a rectangular low-pass filter (sinc interpolation in the time domain).\n"
        "- Practical systems combine an analog anti-aliasing filter with oversampling and digital decimation.\n",
        encoding="utf-8",
    )
    (dsp_topics_dir / "aliasing.md").write_text(
        "# Aliasing & Folding\n\n"
        "When $f_s < 2 f_{\\max}$, spectral replicas overlap. Any frequency component above "
        "the Nyquist frequency $f_s / 2$ folds back into $[0, f_s / 2]$ and cannot be distinguished "
        "from true baseband components after sampling.\n\n"
        "- Always place an analog anti-aliasing filter before the ADC.\n"
        "- In control loops, high-frequency sensor noise aliases into low-frequency drift if unfiltered.\n",
        encoding="utf-8",
    )
    (dsp_topics_dir / "filters.md").write_text(
        "# Digital Filter Design\n\n"
        "Digital filters shape the spectrum of discrete-time signals using linear difference equations:\n\n"
        "- **FIR (Finite Impulse Response):** Always bounded-input bounded-output stable and achieves exact linear phase (constant group delay across all frequencies).\n"
        "- **IIR (Infinite Impulse Response):** Achieves steep transition bands with far fewer multiply-accumulate stages, at the cost of nonlinear phase.\n",
        encoding="utf-8",
    )

    rob_topics_dir = seed_dir / "learning" / "demo-robotics-kinematics" / "topics"
    rob_topics_dir.mkdir(parents=True, exist_ok=True)
    (rob_topics_dir / "rigid-motions.md").write_text(
        "# Configuration Space & Rigid Motions\n\n"
        "Every rigid-body displacement in three-dimensional space can be represented as a screw motion (rotation about an axis combined with translation along that same axis).\n\n"
        "- **Rotation Group SO3:** Orthogonal matrices with determinant one.\n"
        "- **Homogeneous Transformations SE3:** Combines rotation $R$ and translation $p$ into a four-by-four matrix.\n",
        encoding="utf-8",
    )

    commit_all(seed_dir, "Seed initial demo Workbench metadata", env=env)
    push_head_to_branch(seed_dir, branch="main", env=env)

    # 4. Clone workbench-data into <laptop>/.workbench/data and write config.toml
    laptop_wb_dir = laptop_home / ".workbench"
    laptop_data_dir = laptop_wb_dir / "data"
    clone_repo(str(remote_bare), laptop_data_dir, branch="main", env=env)

    # Create fixture data for fake GitHub server
    github_fixtures = {
        "owner": "demouser",
        "repos": [
            {
                "name": "weather-station",
                "full_name": "demouser/weather-station",
                "description": "Solar-powered sensor node that logs temperature and humidity.",
                "fork": False,
                "archived": False,
                "root_commit": ws_root,
            },
            {
                "name": "robot-arm-firmware",
                "full_name": "demouser/robot-arm-firmware",
                "description": "Stepper and encoder loop.",
                "fork": False,
                "archived": False,
                "root_commit": ra_fw_root,
            },
            {
                "name": "robot-arm-planner",
                "full_name": "demouser/robot-arm-planner",
                "description": "Inverse kinematics and trajectory planner.",
                "fork": False,
                "archived": False,
                "root_commit": ra_pl_root,
            },
            {
                "name": "home-lab-cluster",
                "full_name": "demouser/home-lab-cluster",
                "description": "Declarative NixOS and container stack.",
                "fork": False,
                "archived": False,
                "root_commit": hl_root,
            },
            {
                "name": "esp32-audio-dsp",
                "full_name": "demouser/esp32-audio-dsp",
                "description": "Real-time I2S parametric equalizer.",
                "fork": False,
                "archived": False,
                "root_commit": au_root,
            },
            {
                "name": "fpga-logic-analyzer",
                "full_name": "demouser/fpga-logic-analyzer",
                "description": "High-speed protocol capture over USB.",
                "fork": False,
                "archived": False,
                "root_commit": la_root,
            },
            {
                "name": "demo-habit-tracker",
                "full_name": "demouser/demo-habit-tracker",
                "description": "Minimal CLI habit log discovered from GitHub.",
                "fork": False,
                "archived": False,
                "root_commit": ht_root,
            },
            {
                "name": "forked-upstream-tool",
                "full_name": "demouser/forked-upstream-tool",
                "description": "Forked repo that should be skipped.",
                "fork": True,
                "archived": False,
                "root_commit": tp_root,
            },
            {
                "name": "archived-experiment",
                "full_name": "demouser/archived-experiment",
                "description": "Archived repo that should be skipped.",
                "fork": False,
                "archived": True,
                "root_commit": og_root,
            },
        ],
    }
    fixtures_path = base_dir / "github_fixtures.json"
    fixtures_path.write_text(json.dumps(github_fixtures, indent=2) + "\n", encoding="utf-8")

    cfg = WorkbenchLocalConfig(
        data_repo_url=str(remote_bare),
        branch="main",
        scan_roots=[str(code_dir)],
        github_owners=["demouser"],
        user_emails=["demo@example.invalid"],
        privacy_review="auto",
        reviewer_command="claude -p --model haiku",
    )
    save_local_config(cfg, home_dir=laptop_home, env=env)

    # Clean up temporary seed working directory
    import shutil

    shutil.rmtree(seed_dir, ignore_errors=True)

    return {
        "base_dir": str(base_dir),
        "remote_bare": str(remote_bare),
        "laptop_home": str(laptop_home),
        "laptop_data_dir": str(laptop_data_dir),
        "code_dir": str(code_dir),
        "github_fixtures": str(fixtures_path),
    }


class FakeGitHubServer:
    """Tiny local fixture-backed HTTP server for GitHub REST discovery in demos."""

    def __init__(self, fixtures_path: Path) -> None:
        self.fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        fixtures = self.fixtures

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                owner = fixtures.get("owner", "demouser")
                repos = fixtures.get("repos", [])
                if parsed.path == f"/users/{owner}/repos":
                    body = json.dumps(repos).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                    return

                for r in repos:
                    if parsed.path == f"/repos/{r['full_name']}/commits":
                        body = json.dumps([{"sha": r["root_commit"]}]).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(body)
                        return

                self.send_response(404)
                self.end_headers()

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
