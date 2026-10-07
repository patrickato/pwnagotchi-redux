# Source revisions are immutable. APT packages are recorded in the image manifest;
# this is a source-pinned build, not a bit-for-bit reproducible APT snapshot.
PI_GEN_URL=https://github.com/RPi-Distro/pi-gen.git
PI_GEN_REV=1c2abf50924d5bfee3527657af74ddfb1da52904
# This maintained nexmon branch supplies the 6.12/6.18 brcmfmac drivers absent
# from seemoo-lab master. Only firmware, driver and nexutil code is reused.
NEXMON_URL=https://github.com/jayofelony/nexmon.git
NEXMON_REV=1654e1857766df92086dbfbed5ffd288efc9bd8c
