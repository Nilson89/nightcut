from nightcut.export_xmeml import _pathurl


def test_pathurl_keeps_drive_letter():
    assert _pathurl("Z:/FOOTAGE/My Clip.mp4") == "file://localhost/Z:/FOOTAGE/My%20Clip.mp4"


def test_pathurl_unc_share():
    assert _pathurl("//nas/cut/clip.mp4") == "file://nas/cut/clip.mp4"


def test_xml_has_every_track_and_mix_switched_off(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET

    from nightcut import export_xmeml
    from nightcut.media import AudioStream, MediaInfo
    from nightcut.tracks import timeline_tracks

    streams = [AudioStream(i + 1, i, t, ch, 48000) for i, (t, ch) in enumerate([("Mix", 2), ("Mic", 1), ("Game", 2), ("Discord", 2)])]
    rec = MediaInfo(tmp_path / "rec.mkv", 600.0, 1920, 1080, 60.0, streams)
    monkeypatch.setattr(export_xmeml, "probe", lambda p: MediaInfo(p, 20.0, 1920, 1080, 60.0, streams))
    audio = timeline_tracks(rec, {"mic": streams[1], "game": streams[2], "party": streams[3]}, mix=streams[0])
    clips = [{"n": 1, "start": 10.0, "end": 20.0, "trim_in": 3.0, "gameplay_file": str(tmp_path / "c1.mp4"),
              "facecam_file": None, "title": "Teil 1"}]
    out = export_xmeml.write(clips, audio, {"export": {"facecam_pip": {}}}, tmp_path / "t.xml", "test")

    root = ET.parse(out).getroot()
    a_tracks = root.findall("./sequence/media/audio/track")
    assert len(a_tracks) == 4
    assert a_tracks[0].findtext("enabled") == "FALSE"
    assert [t.find("clipitem").findtext("enabled") for t in a_tracks] == ["FALSE", "TRUE", "TRUE", "TRUE"]
    assert [t.find("clipitem").findtext("name") for t in a_tracks] == ["01 Teil 1 MIX", "01 Teil 1 MIC", "01 Teil 1 GAME", "01 Teil 1 PARTY"]
    assert [a.findtext("channelcount") for a in root.iter("file") for a in a.findall("./media/audio")] == ["2", "1", "2", "2"]
