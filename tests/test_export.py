from nightcut.export_xmeml import _pathurl


def test_pathurl_keeps_drive_letter():
    assert _pathurl("Z:/FOOTAGE/My Clip.mp4") == "file://localhost/Z:/FOOTAGE/My%20Clip.mp4"


def test_pathurl_unc_share():
    assert _pathurl("//nas/cut/clip.mp4") == "file://nas/cut/clip.mp4"
