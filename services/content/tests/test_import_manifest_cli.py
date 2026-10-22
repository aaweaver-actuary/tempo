"""Database-free PGN previews publish complete manifests without replacing files."""

import hashlib
import json
import os
from pathlib import Path

import psycopg
import pytest

from tempo_content import cli
from tempo_content.bridge import ImportManifest, map_import


@pytest.fixture(autouse=True)
def forbid_database_access(monkeypatch):
    protected = {
        'TEMPO_CONTENT_WORKSPACE', 'TEMPO_CONTENT_DATABASE_URL',
        'TEMPO_CONTENT_MIGRATION_DATABASE_URL', 'DATABASE_URL',
    }
    for name in protected:
        monkeypatch.delenv(name, raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError('Manifest previews must not enter database code')

    for name in ('ContentService', 'migrate', 'readiness_from_url'):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(psycopg, 'connect', forbidden)
    monkeypatch.setattr(psycopg.Connection, 'connect', forbidden)

    original_getitem = type(os.environ).__getitem__

    def guarded_getitem(environment, key):
        if key in protected:
            raise AssertionError('Manifest previews must not read database configuration')
        return original_getitem(environment, key)

    monkeypatch.setattr(type(os.environ), '__getitem__', guarded_getitem)


@pytest.fixture
def preview_files(tmp_path):
    raw = b'[Event "Synthetic preview"]\n\n1. e4 e5 2. Nf3 *\n'
    source = tmp_path / 'opening.pgn'
    source.write_bytes(raw)
    return source, tmp_path / 'opening.manifest.json', raw


def preview_arguments(source, output):
    return ['import', 'manifest', str(source), '--workspace', 'preview-test',
            '--source-key', 'opening', '--title', 'Synthetic opening',
            '--color', 'white', '--output', str(output)]


def assert_failure(result, capsys, output):
    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    error = json.loads(captured.err)
    assert error['status'] == 'error'
    assert not output.exists()
    assert not list(output.parent.glob('.tempo-content-manifest-*.tmp'))
    return error


@pytest.mark.parametrize('color,exercise_count', [('white', 2), ('black', 1)])
def test_manifest_preview_saves_raw_schema_and_reports_validated_counts_without_database(
    preview_files, capsys, color, exercise_count,
):
    source, output, raw = preview_files
    arguments = preview_arguments(source, output)
    arguments[arguments.index('--color') + 1] = color
    assert cli.main(arguments) == 0
    captured = capsys.readouterr()
    assert captured.err == ''
    assert json.loads(captured.out) == {
        'status': 'validated', 'manifest_path': str(output),
        'source_sha256': hashlib.sha256(raw).hexdigest(),
        'route_count': 1, 'exercise_count': exercise_count,
    }
    manifest = ImportManifest.model_validate_json(output.read_bytes())
    assert manifest.workspace_id == 'preview-test'
    assert manifest.source_key == 'opening'
    assert manifest.title == 'Synthetic opening'
    assert manifest.repertoires[0].learner_color == color
    assert manifest.complete is True
    assert manifest.legacy_aliases == {}
    assert manifest.historical_mapping_unknown == []
    assert manifest.reentry == []
    mapped, _ = map_import(raw, json.loads(output.read_text()))
    assert len(mapped.routes) == 1
    assert len(mapped.exercises) == exercise_count
    assert set(output.parent.iterdir()) == {source, output}


@pytest.mark.parametrize('prefix', [b'{An authored comment}\n', b' \n\t{An authored comment}\n'])
def test_preview_accepts_leading_brace_comment_and_saved_manifest_maps_the_exact_bytes(
    preview_files, capsys, prefix,
):
    source, output, _ = preview_files
    raw = prefix + b'1. e4 e5 2. Nf3 *\n'
    source.write_bytes(raw)

    assert cli.main(preview_arguments(source, output)) == 0

    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'validated'
    assert result['source_sha256'] == hashlib.sha256(raw).hexdigest()
    manifest = ImportManifest.model_validate_json(output.read_bytes())
    assert manifest.provenance.source_sha256 == hashlib.sha256(raw).hexdigest()
    source_snapshot, _ = map_import(raw, manifest.model_dump(mode='json'))
    assert len(source_snapshot.routes) == 1
    assert source_snapshot.routes[0].moves == ['e2e4', 'e7e5', 'g1f3']


@pytest.mark.parametrize('missing', ['file', '--workspace', '--source-key', '--title', '--color', '--output'])
def test_manifest_preview_requires_file_and_every_flag(preview_files, capsys, missing):
    source, output, _ = preview_files
    arguments = preview_arguments(source, output)
    index = 2 if missing == 'file' else arguments.index(missing)
    del arguments[index:index + (1 if missing == 'file' else 2)]
    with pytest.raises(SystemExit) as caught:
        cli.main(arguments)
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'required' in captured.err
    assert not output.exists()
    assert set(output.parent.iterdir()) == {source}


def test_manifest_preview_rejects_unknown_color(preview_files, capsys):
    source, output, _ = preview_files
    arguments = preview_arguments(source, output)
    arguments[arguments.index('--color') + 1] = 'grey'
    with pytest.raises(SystemExit) as caught:
        cli.main(arguments)
    assert caught.value.code == 2
    assert 'invalid choice' in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize('existing', ['file', 'directory', 'symlink'])
def test_existing_output_paths_are_never_replaced(preview_files, capsys, existing):
    source, output, _ = preview_files
    original = b'\x00Existing output must remain byte-identical\xff'
    target = output.parent / 'target'
    if existing == 'file':
        output.write_bytes(original)
    elif existing == 'directory':
        output.mkdir()
        (output / 'kept').write_bytes(original)
    else:
        target.write_bytes(original)
        output.symlink_to(target)
    assert cli.main(preview_arguments(source, output)) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err)['status'] == 'error'
    if existing == 'directory':
        assert (output / 'kept').read_bytes() == original
    else:
        assert output.read_bytes() == original
    if existing == 'symlink':
        assert output.is_symlink()
        assert output.readlink() == target
    assert not list(output.parent.glob('.tempo-content-manifest-*.tmp'))


def test_invalid_pgn_produces_no_output(preview_files, capsys):
    source, output, _ = preview_files
    source.write_bytes(b'1. e4 e5 2. Bh6 *\n')
    error = assert_failure(cli.main(preview_arguments(source, output)), capsys, output)
    assert error['code'] == 'invalid_pgn'
    assert set(output.parent.iterdir()) == {source}


@pytest.mark.parametrize('parent_kind', ['missing', 'file'])
def test_output_directory_must_already_exist_and_be_a_directory(preview_files, capsys, parent_kind):
    source, _, _ = preview_files
    parent = source.parent / 'unavailable'
    if parent_kind == 'file':
        parent.write_bytes(b'Existing file')
    output = parent / 'opening.json'
    error = assert_failure(cli.main(preview_arguments(source, output)), capsys, output)
    assert error['code'] == 'invalid_output_directory'
    assert not parent.is_dir()
    if parent_kind == 'file':
        assert parent.read_bytes() == b'Existing file'


def test_missing_input_file_returns_sanitized_error(preview_files, capsys):
    source, output, _ = preview_files
    source.unlink()
    error = assert_failure(cli.main(preview_arguments(source, output)), capsys, output)
    assert error['code'] == 'invalid_input'
    assert str(source) not in error['message']


@pytest.mark.parametrize('stage', ['write', 'flush', 'fsync', 'link'])
def test_write_failures_leave_no_partial_output_and_remove_only_the_owned_temporary_file(
    preview_files, capsys, monkeypatch, stage,
):
    source, output, raw = preview_files
    unrelated = source.parent / '.tempo-content-manifest-other-task.tmp'
    unrelated.write_bytes(b'Keep another task file')
    secret = 'private filesystem or credential details'

    def fail(*args, **kwargs):
        raise OSError(secret)

    if stage in ('write', 'flush'):
        create_temporary = cli.tempfile.NamedTemporaryFile

        def failing_temporary(*args, **kwargs):
            temporary = create_temporary(*args, **kwargs)
            write = temporary.write

            def partial_write(value):
                write(value[:10])
                fail()

            setattr(temporary, stage, partial_write if stage == 'write' else fail)
            return temporary

        monkeypatch.setattr(cli.tempfile, 'NamedTemporaryFile', failing_temporary)
    else:
        monkeypatch.setattr(cli.os, stage, fail)
    assert cli.main(preview_arguments(source, output)) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err)['code'] == 'invalid_input'
    assert secret not in captured.err
    assert not output.exists()
    assert source.read_bytes() == raw
    assert unrelated.read_bytes() == b'Keep another task file'
    assert set(output.parent.iterdir()) == {source, unrelated}


def test_destination_created_at_publication_time_is_not_overwritten(preview_files, capsys, monkeypatch):
    source, output, raw = preview_files
    link = cli.os.link
    concurrent_bytes = b'Concurrent creator wins'

    def concurrent_link(temporary, destination):
        temporary_path = Path(temporary)
        assert temporary_path.parent == output.parent
        manifest = ImportManifest.model_validate_json(temporary_path.read_bytes())
        assert manifest.provenance.source_sha256 == hashlib.sha256(raw).hexdigest()
        assert Path(destination) == output
        output.write_bytes(concurrent_bytes)
        link(temporary, destination)

    monkeypatch.setattr(cli.os, 'link', concurrent_link)
    assert cli.main(preview_arguments(source, output)) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err)['status'] == 'error'
    assert output.read_bytes() == concurrent_bytes
    assert set(output.parent.iterdir()) == {source, output}


def test_input_read_is_bounded_and_oversized_input_never_reaches_the_parser(preview_files, capsys, monkeypatch):
    import tempo_content.import_manifest as generator

    source, output, _ = preview_files
    open_path = Path.open
    reads = []
    limit = 16 * 1024 * 1024

    class OversizedInput:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            reads.append(size)
            assert size == limit + 1
            return b'x' * size

    def bounded_open(path, *args, **kwargs):
        if path == source:
            assert args == ('rb',)
            return OversizedInput()
        return open_path(path, *args, **kwargs)

    def unexpected_parser(*args, **kwargs):
        raise AssertionError('Oversized PGN must be rejected before parsing')

    monkeypatch.setattr(Path, 'open', bounded_open)
    monkeypatch.setattr(generator, 'make_manifest', unexpected_parser)
    error = assert_failure(cli.main(preview_arguments(source, output)), capsys, output)
    assert error['code'] == 'source_byte_bound'
    assert reads == [limit + 1]
