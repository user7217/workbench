# Homebrew cask for the Workbench macOS app, published in the tap user7217/homebrew-workbench.
# This file is the source: the release workflow fills in version and sha256 for each release and
# commits the result to the tap as Casks/workbench-dashboard.rb (see docs/releasing.md).
cask "workbench-dashboard" do
  version "@VERSION@"
  sha256 "@SHA256@"

  url "https://github.com/user7217/workbench/releases/download/v#{version}/Workbench_#{version}_aarch64.dmg"
  name "Workbench"
  desc "Dashboard of projects and learning paths kept current by coding agents"
  homepage "https://github.com/user7217/workbench"

  livecheck do
    url :url
    strategy :github_latest
  end

  # The app installs its own updates (signed, from GitHub Releases), so `brew upgrade` leaves it
  # alone unless run with --greedy
  auto_updates true
  depends_on arch: :arm64
  depends_on macos: :monterey

  app "Workbench.app"
  # The same CLI the app's server runs, so terminal, agent hooks and dashboard share one version
  binary "#{appdir}/Workbench.app/Contents/Resources/workbench/workbench"

  # The app is not signed by Apple yet, so with the download's quarantine flag macOS refuses to
  # open it ("damaged"). Clearing the flag is what docs/desktop.md asks for on a manual install.
  # must_succeed: false, since there is no flag to clear when Homebrew did not set one.
  postflight_steps steps: [{
    "type"          => "run",
    "command"       => { "path" => "/usr/bin/xattr" },
    "args"          => ["-dr", "com.apple.quarantine", "#{appdir}/Workbench.app"],
    "allow_failure" => true,
  }]

  uninstall launchctl: "Workbench",
            quit:      "io.github.user7217.workbench",
            delete:    "~/Library/LaunchAgents/Workbench.plist"

  # ~/.workbench (config, data clone, logs) is shared with the CLI and kept
  zap trash: [
    "~/Library/Application Support/io.github.user7217.workbench",
    "~/Library/Caches/io.github.user7217.workbench",
    "~/Library/WebKit/io.github.user7217.workbench",
  ]
end
