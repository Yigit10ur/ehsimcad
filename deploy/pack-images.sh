#!/usr/bin/env bash
#
# Build the images here, so the server does not have to build them there.
#
# A closed network cannot build this. The build reaches Docker Hub for two base
# images, npm for the web dependencies, PyPI for OpenCascade and a Debian
# mirror for four graphics libraries -- and none of that is reachable from a
# machine with no way out. So the build happens on a machine that does have the
# internet, and what travels to the server is the result.
#
#   ./deploy/pack-images.sh                    # for an x86-64 server
#   ./deploy/pack-images.sh linux/arm64        # for an ARM one
#
# It leaves one file. Carry it over, and on the server:
#
#   docker load -i ehsimcad_v1-images-linux-amd64.tar.gz
#
# after which the ordinary instructions in INSTALL.md run unchanged, offline.
#
# Nothing secret goes into the archive: the build reads no configuration, which
# is why one archive serves every environment.

set -euo pipefail

PLATFORM="${1:-linux/amd64}"

# Made and resolved before the `cd` below, so a relative path means where you
# are standing rather than where the repository happens to be.
OUT_DIR="${2:-.}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

cd "$(dirname "$0")/.."

ARCHIVE="$OUT_DIR/ehsimcad_v1-images-${PLATFORM//\//-}.tar.gz"

# The architecture the server runs, not the one this machine runs. Building for
# the wrong one is the failure this script exists to make hard: the images load
# without complaint and every container then dies with "exec format error".
# Ask the server, before building: `uname -m` says x86_64 or aarch64.
echo "Building for $PLATFORM (this machine is $(docker version --format '{{.Server.Os}}/{{.Server.Arch}}'))."

# Which version of the source is going into the images. There is nothing else
# in the archive that says: an image carries no configuration, and no commit.
if commit=$(git rev-parse --short HEAD 2>/dev/null); then
  dirty=""
  git diff --quiet HEAD 2>/dev/null || dirty=" (with uncommitted changes)"
  echo "Source: $commit$dirty"
fi
echo

# Attestations turn a single image into a manifest list, which older `docker
# load` on the far end does not always take. There is nothing here that needs
# them.
export BUILDX_NO_DEFAULT_ATTESTATIONS=1
export DOCKER_DEFAULT_PLATFORM="$PLATFORM"

# --pull, because the point of the exercise is that the server cannot fetch a
# base image later. Take the current one now.
docker compose --profile tools build --pull

# Postgres, MinIO and `mc` are not built here -- they are somebody else's
# images, named by exact release in compose.yaml. They still have to be in the
# archive: on a closed network a missing base image is as fatal as a missing
# one of ours, and Compose would try to fetch it at `up`.
docker compose --profile tools pull --ignore-buildable --policy always

IMAGES=$(docker compose --profile tools config --images | sort -u)

# The build can quietly produce host-architecture images -- if emulation for
# the target is not installed, or if a stage was served from a cache built
# earlier for something else. Both look like success. Check what was actually
# made.
# The architecture on its own: `linux/arm64/v8` names a variant that
# `.Architecture` does not carry.
WANT="$(echo "$PLATFORM" | cut -d/ -f2)"

# Ask about the platform we want, not about the image in general.
#
# `docker image inspect` on its own answers with *this machine's* architecture
# whenever the daemon keeps images the containerd way: a pulled base image
# holds every platform its publisher built, and inspect picks the one that
# matches the host. So the plain check called a perfectly good amd64 archive
# arm64 and refused to pack it, on a machine where all six images were right.
#
# `--platform` asks the question that was meant. It fails when the image does
# not carry that platform, which is the case worth refusing -- and the fallback
# below then reports the host architecture, which will not match `$WANT`
# either, so an older CLI without the flag still refuses rather than shipping
# something unusable.
arch_of() {
  docker image inspect --platform "$PLATFORM" "$1" --format '{{.Architecture}}' 2>/dev/null \
    || docker image inspect "$1" --format '{{.Architecture}}' 2>/dev/null \
    || echo "unreadable"
}

for image in $IMAGES; do
  got=$(arch_of "$image")
  if [ "$got" != "$WANT" ]; then
    echo "Refusing to pack: $image is $got, and the server needs $WANT." >&2
    echo "Emulation for $PLATFORM is probably not installed on this machine." >&2
    exit 1
  fi
done

echo
echo "Packing:"
for image in $IMAGES; do
  printf '  %-28s %s\n' "$image" "$(docker image inspect "$image" --format '{{.Size}}' | awk '{printf "%.0f MB", $1/1048576}')"
done

# Through gzip because `docker save` is not consistent about it: a daemon
# keeping images the older way writes the layers uncompressed, and this is then
# the difference between a gigabyte and four. A daemon using containerd writes
# them already compressed, and this costs twenty seconds and saves nothing.
# `docker load` takes either.
# `--platform` for the same reason as the check above: a base image kept the
# containerd way carries every platform it was published for, and without this
# the archive would hold six architectures of Postgres to deliver one. Older
# CLIs have no such flag; there the image is single-platform anyway.
SAVE_PLATFORM=""
if docker save --help 2>&1 | grep -q -- '--platform'; then
  SAVE_PLATFORM="--platform $PLATFORM"
fi

# shellcheck disable=SC2086
docker save $SAVE_PLATFORM $IMAGES | gzip > "$ARCHIVE"

echo
echo "$ARCHIVE"
ls -lh "$ARCHIVE" | awk '{print "  " $5}'
shasum -a 256 "$ARCHIVE" 2>/dev/null || sha256sum "$ARCHIVE"
