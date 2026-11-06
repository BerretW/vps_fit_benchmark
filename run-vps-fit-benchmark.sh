#!/usr/bin/env bash
set -Eeuo pipefail

readonly script_url="https://raw.githubusercontent.com/berretw/vps_fit_benchmark/main/vps-fit-benchmark.py"

if ! command -v curl >/dev/null 2>&1; then
    printf 'Error: curl is required to download the benchmark.\n' >&2
    exit 1
fi

missing_packages=()
for dependency in python3 sysbench fio; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
        missing_packages+=("$dependency")
    fi
done

if ((${#missing_packages[@]})); then
    if ! command -v apt-get >/dev/null 2>&1; then
        printf 'Error: missing dependencies (%s); automatic installation supports Debian/Ubuntu (apt-get) only.\n' \
            "${missing_packages[*]}" >&2
        exit 1
    fi

    if ((EUID == 0)); then
        apt_get=(apt-get)
    elif command -v sudo >/dev/null 2>&1; then
        apt_get=(sudo apt-get)
    else
        printf 'Error: install missing dependencies with apt-get, or run this command with sudo available.\n' >&2
        exit 1
    fi

    "${apt_get[@]}" update
    "${apt_get[@]}" install -y "${missing_packages[@]}"
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 8))'; then
    printf 'Error: Python 3.8 or newer is required; found %s.\n' "$(python3 --version 2>&1)" >&2
    exit 1
fi

tmp_script="$(mktemp)"
trap 'rm -f -- "$tmp_script"' EXIT

curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
    "$script_url" --output "$tmp_script"

python3 "$tmp_script" "$@"
