#!/usr/bin/env bash
# Provision a fresh Ubuntu 26.04 LTS GPU VDS (NVIDIA A4000) for Track 2 OCR.
# Installs: NVIDIA driver, Docker Engine, NVIDIA Container Toolkit.
# Run as root (or with sudo).  A reboot is required after the driver install.
set -euo pipefail

log() { echo -e "\n\033[1;32m==> $*\033[0m"; }

log "1/5 Base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release ubuntu-drivers-common

log "2/5 NVIDIA driver (open kernel modules)"
# Autodetect + install the recommended driver for this GPU.
# If `ubuntu-drivers` can't pick one on 26.04 yet, install explicitly:
#   apt-get install -y nvidia-driver-580-open   (adjust version)
ubuntu-drivers autoinstall || apt-get install -y nvidia-driver-580-open

log "3/5 Docker Engine"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
# NOTE: if Docker has no 26.04 ("resolute"?) repo yet, pin to the latest LTS
# codename that exists, e.g. replace "$(. /etc/os-release; echo "$VERSION_CODENAME")"
# with the previous LTS codename.
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release; echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

log "4/5 NVIDIA Container Toolkit"
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

log "5/5 Done. REBOOT now to load the NVIDIA driver:  sudo reboot"
echo "After reboot, verify with:"
echo "  nvidia-smi"
echo "  docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu24.04 nvidia-smi"
echo "Then, from track2-ocr/:"
echo "  cp .env.example .env   # edit if needed"
echo "  mkdir -p data/pdfs data/output"
echo "  # upload your PDF corpus into data/pdfs/"
echo "  docker compose up -d vllm-server"
echo "  docker compose logs -f vllm-server   # wait for 'ready'"
echo "  docker compose run --rm batch"
