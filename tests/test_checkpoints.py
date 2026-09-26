from raven.recovery.checkpoints import CheckpointManager


def test_restore_recreates_original_content(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("original")
    mgr = CheckpointManager(tmp_path)
    mgr.snapshot("a.txt")
    f.write_text("modified")
    mgr.restore_to_clean()
    assert f.read_text() == "original"


def test_restore_deletes_newly_created_file(tmp_path):
    mgr = CheckpointManager(tmp_path)
    mgr.snapshot("new.txt")  # file doesn't exist yet
    (tmp_path / "new.txt").write_text("created")
    mgr.restore_to_clean()
    assert not (tmp_path / "new.txt").exists()


def test_snapshot_only_keeps_first_pre_write_state(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("v1")
    mgr = CheckpointManager(tmp_path)
    mgr.snapshot("a.txt")
    f.write_text("v2")
    mgr.snapshot("a.txt")  # should be a no-op, v1 already captured
    f.write_text("v3")
    mgr.restore_to_clean()
    assert f.read_text() == "v1"


def test_has_changes_and_touched_paths(tmp_path):
    mgr = CheckpointManager(tmp_path)
    assert not mgr.has_changes()
    mgr.snapshot("x.py")
    assert mgr.has_changes()
    assert mgr.touched_paths == ["x.py"]
