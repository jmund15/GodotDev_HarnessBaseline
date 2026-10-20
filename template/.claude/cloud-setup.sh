#!/usr/bin/env bash
# Cloud toolchain for a Godot C# project: .NET SDK, headless Godot Mono, xvfb.
#
# Paste this whole file into the claude.ai/code environment's "Setup script" field. It runs as
# root before Claude Code launches and the result is snapshotted for ~7 days, so later sessions
# start with the toolchain installed. It needs no repo checkout.
# hooks/session_context_loader.py runs it from SessionStart when the environment has no setup script,
# which costs the install on every new session instead of once per snapshot.
#
# Must exit 0: a failing setup script blocks the session. Each step reports and continues.
set -u

# Pins must match .claude/reference/project_stack.md.
GODOT_VERSION="4.7.1"
GODOT_RELEASE="stable"
DOTNET_CHANNEL="9.0"
GODOT_MCP_VERSION="3.8.0"

# The digest of this file with this line's value blanked. Refresh it in the same change as any edit
# above or below: `python3 .claude/tools/workstation_profile.py fingerprint --write .claude/cloud-setup.sh`.
# The script writes the value to ${PREFIX}/.cloud-setup.fingerprint below, which is how
# SessionStart tells a snapshot built from an older paste (paste this file again) from one built
# from the repo's copy. `tests/test_setup_surface_parity.py` fails when the stored value is stale.
SETUP_FINGERPRINT="b7cc6c22fbe1effd69d82bc3ad3113570adf2a78db15e14756db41398667e0b9"

if [ "$(id -u)" = "0" ]; then SUDO=""; PREFIX="/opt"
elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then SUDO="sudo"; PREFIX="/opt"
else SUDO=""; PREFIX="${HOME}/.local"
fi
DOTNET_ROOT="${PREFIX}/dotnet"
GODOT_NAME="Godot_v${GODOT_VERSION}-${GODOT_RELEASE}_mono_linux_x86_64"
GODOT_DIR="${PREFIX}/godot"
GODOT_BIN="${GODOT_DIR}/${GODOT_NAME}/Godot_v${GODOT_VERSION}-${GODOT_RELEASE}_mono_linux.x86_64"
FAILED=()

echo "[cloud-setup] prefix=${PREFIX}"

missing_pkgs=()
command -v xvfb-run >/dev/null 2>&1 || missing_pkgs+=(xvfb xauth)
command -v unzip >/dev/null 2>&1 || missing_pkgs+=(unzip)
command -v curl >/dev/null 2>&1 || missing_pkgs+=(curl ca-certificates)
# The Godot MCP server is a node package run through `npx` (tools/godot_mcp_launch.cjs). A
# snapshot without node cannot start the server at all, so it is a setup dependency, not a nicety.
command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 || missing_pkgs+=(nodejs npm)
# Godot's X11 driver loads these even under Xvfb.
for lib in libfontconfig.so.1 libXcursor.so.1 libXinerama.so.1 libXrandr.so.2 libXi.so.6 libGL.so.1; do
    ldconfig -p 2>/dev/null | grep -q "$lib" || { missing_pkgs+=(libfontconfig1 libxcursor1 libxinerama1 libxrandr2 libxi6 libgl1); break; }
done
if [ ${#missing_pkgs[@]} -gt 0 ]; then
    if [ -n "${SUDO}" ] || [ "$(id -u)" = "0" ]; then
        echo "[cloud-setup] apt install ${missing_pkgs[*]}"
        ${SUDO} apt-get update -qq >/dev/null 2>&1
        DEBIAN_FRONTEND=noninteractive ${SUDO} apt-get install -y -qq "${missing_pkgs[@]}" >/dev/null 2>&1 \
            || FAILED+=("apt install ${missing_pkgs[*]}")
    else
        FAILED+=("apt packages ${missing_pkgs[*]} (no root)")
    fi
fi

if "${DOTNET_ROOT}/dotnet" --list-sdks 2>/dev/null | grep -q "^${DOTNET_CHANNEL}"; then
    echo "[cloud-setup] .NET ${DOTNET_CHANNEL}: present"
else
    echo "[cloud-setup] installing .NET ${DOTNET_CHANNEL} SDK"
    if curl -fsSL --max-time 60 https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh; then
        ${SUDO} bash /tmp/dotnet-install.sh --channel "${DOTNET_CHANNEL}" --install-dir "${DOTNET_ROOT}" >/dev/null 2>&1 \
            || FAILED+=(".NET SDK install")
    else
        FAILED+=(".NET installer download (dot.net)")
    fi
fi
if [ -x "${DOTNET_ROOT}/dotnet" ] && [ "${PREFIX}" = "/opt" ]; then
    ${SUDO} ln -sf "${DOTNET_ROOT}/dotnet" /usr/local/bin/dotnet 2>/dev/null || true
fi

if [ -x "${GODOT_BIN}" ]; then
    echo "[cloud-setup] Godot ${GODOT_VERSION}: present"
else
    echo "[cloud-setup] installing Godot ${GODOT_VERSION} Mono"
    zip="/tmp/${GODOT_NAME}.zip"
    got=""
    for url in \
        "https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}-${GODOT_RELEASE}/${GODOT_NAME}.zip" \
        "https://downloads.godotengine.org/?version=${GODOT_VERSION}&flavor=${GODOT_RELEASE}&slug=mono_linux_x86_64.zip&platform=linux.64"; do
        if curl -fsSL --max-time 180 "$url" -o "$zip" && unzip -tq "$zip" >/dev/null 2>&1; then got="$url"; break; fi
    done
    if ! command -v unzip >/dev/null 2>&1; then
        FAILED+=("Godot install (unzip missing)")
    elif [ -n "$got" ]; then
        ${SUDO} mkdir -p "${GODOT_DIR}"
        { ${SUDO} unzip -qo "$zip" -d "${GODOT_DIR}" && ${SUDO} chmod +x "${GODOT_BIN}"; }             || FAILED+=("Godot extract into ${GODOT_DIR}")
        rm -f "$zip"
    else
        FAILED+=("Godot download (github release assets and downloads.godotengine.org both failed)")
    fi
fi

# The engine pin the Godot MCP launcher reads when GODOT_PATH is unset (cloud has no GODOT_BIN):
# `<prefix>/godot/GODOT_BIN`, one absolute path per line. Written whether the engine was installed
# now or was already present, and under the same prefix the launcher searches (/opt, then ~/.local).
${SUDO} mkdir -p "${GODOT_DIR}" 2>/dev/null || true
printf '%s\n' "${GODOT_BIN}" | ${SUDO} tee "${GODOT_DIR}/GODOT_BIN" >/dev/null 2>&1 \
    || FAILED+=("engine pointer file ${GODOT_DIR}/GODOT_BIN")

# `npx -y` resolves the package from its own `_npx` tree, which a fresh snapshot has never filled
# and an MCP server start cannot fill without network. `npm cache add` fills the package cache, and
# one `npx -y <spec> --version` (stdin closed, so a server that reads stdio cannot block) creates
# that tree. GODOT_PATH is set so the runtime's own engine validation sees the pinned engine.
if command -v npx >/dev/null 2>&1; then
    echo "[cloud-setup] warming the npx cache for godot-mcp-runtime@${GODOT_MCP_VERSION}"
    GODOT_PATH="${GODOT_BIN}" npm cache add "godot-mcp-runtime@${GODOT_MCP_VERSION}" >/dev/null 2>&1 \
        || FAILED+=("npm cache add godot-mcp-runtime@${GODOT_MCP_VERSION}")
    GODOT_PATH="${GODOT_BIN}" npx -y "godot-mcp-runtime@${GODOT_MCP_VERSION}" --version \
        < /dev/null >/dev/null 2>&1 \
        || FAILED+=("npx warmup godot-mcp-runtime@${GODOT_MCP_VERSION}")
else
    FAILED+=("node/npm absent: the godot MCP server (npx) cannot start")
fi

# Login shells read these; SessionStart persists the same values through CLAUDE_ENV_FILE for hooks
# and the Bash tool.
if [ "${PREFIX}" = "/opt" ]; then
    printf 'export DOTNET_ROOT=%s\nexport PATH="%s:$PATH"\nexport GODOT_BIN=%s\nexport DOTNET_CLI_TELEMETRY_OPTOUT=1\n' \
        "${DOTNET_ROOT}" "${DOTNET_ROOT}" "${GODOT_BIN}" | ${SUDO} tee /etc/profile.d/godot-toolchain.sh >/dev/null
fi

# The marker records WHICH script ran, not whether every step succeeded, so it is written even when
# one failed above: a half-installed snapshot must still not look like one built from a different
# script. A missing marker reads as "different script" and re-runs this file (session_context_loader).
${SUDO} mkdir -p "${PREFIX}" 2>/dev/null || true
printf '%s\n' "${SETUP_FINGERPRINT}" | ${SUDO} tee "${PREFIX}/.cloud-setup.fingerprint" >/dev/null 2>&1 || true

if [ ${#FAILED[@]} -eq 0 ]; then
    echo "[cloud-setup] OK dotnet=$("${DOTNET_ROOT}/dotnet" --version 2>/dev/null) godot=${GODOT_BIN}"
else
    printf '[cloud-setup] FAILED: %s\n' "${FAILED[@]}"
fi
exit 0
