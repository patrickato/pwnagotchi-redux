"""Install the opt-in A/B export profile into the pinned pi-gen tree."""
from pathlib import Path
import shutil
from export_layout import replace_once


def prepare(source, tree):
    source, tree = Path(source), Path(tree)
    finalise = tree / "export-image/05-finalise/01-run.sh"
    if "redux-ota-clone.sh" in finalise.read_text():
        raise ValueError("A/B export already prepared")
    text = replace_once(finalise.read_text(), 'unmount "${ROOTFS_DIR}"',
                        'source "${STAGE_DIR}/redux-ota-clone.sh"\nunmount "${ROOTFS_DIR}"')
    finalise.write_text(text)
    for name, target in [("ota-prerun.sh", "export-image/prerun.sh"),
                         ("ota-partuuid.sh", "export-image/04-set-partuuid/00-run.sh"),
                         ("ota-clone.sh", "export-image/redux-ota-clone.sh")]:
        shutil.copyfile(source / name, tree / target)
        (tree / target).chmod(0o755)


if __name__ == "__main__":
    import sys
    prepare(*sys.argv[1:])
