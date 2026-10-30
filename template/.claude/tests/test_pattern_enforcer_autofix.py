"""Computed C# attributes reach the Edit/Write input; other denials stay intact."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hooks'))
import pattern_enforcer as pe


def test_required_export_fix():
    content = '[Export]\npublic Node Thing = null!;\n'
    fixed, notes = pe.fixes(content, 'Scripts/Thing.cs')
    assert fixed == '[Export, RequiredExport]\npublic Node Thing = null!;\n'
    assert 'ValidateRequiredExports()' in ' '.join(notes) and 'T?' in ' '.join(notes)
    assert pe.fixes(fixed, 'Scripts/Thing.cs') == (fixed, [])


def test_property_export_fix():
    content = '[Export] public Node Thing { get; set; } = null!;'
    fixed, notes = pe.fixes(content, 'Scripts/Thing.cs')
    assert fixed == content.replace('[Export]', '[Export, RequiredExport]')
    assert notes


def test_uninitialized_property_does_not_borrow_the_next_members_null():
    content = ('[Export] public bool VerboseLog { get; set; }\n'
               '[Export] public float Depth { get; set; }\n'
               'private Node _body = null!;\n')
    assert pe.fixes(content, 'Scripts/Thing.cs') == (content, [])


def test_resource_fix_not_node():
    content = '[GlobalClass]\npublic partial class Thing : Resource {}\n'
    fixed, notes = pe.fixes(content, 'Scripts/Thing.cs')
    assert fixed == content.replace('[GlobalClass]', '[GlobalClass, Tool]')
    assert notes and pe.fixes(fixed, 'Scripts/Thing.cs') == (fixed, [])
    node = content.replace(': Resource', ': Node')
    assert pe.fixes(node, 'Scripts/Thing.cs') == (node, [])


def test_optional_comments_and_existing_attributes_pass():
    for content in ['[Export] public Node? Thing = null;',
                    '[Export] public Node? Thing = null!;',
                    '// [Export] public Node Thing = null!;',
                    'var text = "[Export] public Node Thing = null!;";',
                    '[RequiredExport]\n[Export] public Node Thing = null!;',
                    '[Tool]\n[GlobalClass]\npublic partial class Thing : Resource {}']:
        assert pe.fixes(content, 'Scripts/Thing.cs') == (content, [])
    content = '[Export] public Node Thing = null!;'
    assert pe.fixes(content, 'notes.md') == (content, [])


def test_write_channel():
    payload = {'tool_name': 'Write', 'tool_input': {'file_path': 'Scripts/Thing.cs',
                'content': '[Export] public Node Thing = null!;'}}
    result = pe.process(payload)
    assert not result.get('block'), result
    assert '[Export, RequiredExport]' in result['updatedInput']['content']
    assert 'ValidateRequiredExports()' in result['context']


def test_edit_context_can_fix_attribute_outside_snippet(tmp_path):
    path = tmp_path / 'Thing.cs'
    original = '[Export]\npublic Node Thing = default!;\n'
    path.write_text(original, encoding='utf-8')
    ti = {'file_path': str(path), 'old_string': 'default!', 'new_string': 'null!'}
    result = pe.process({'tool_name': 'Edit', 'tool_input': ti})
    updated = result['updatedInput']
    applied = original.replace(updated['old_string'], updated['new_string'],
                               -1 if updated.get('replace_all') else 1)
    assert applied == '[Export, RequiredExport]\npublic Node Thing = null!;\n'
    assert ti['old_string'] == 'default!'


def test_crlf_edit_matches_live_client_normalization(tmp_path):
    # Captured live Edit probe, session 10749e62-d94f-4520-a85f-12bcd127afe9.
    path = tmp_path / 'Probe.cs'
    path.write_bytes(b'[Export]\r\npublic Node Thing = default!;\r\n')
    result = pe.process({'tool_name': 'Edit', 'tool_input': {
        'file_path': str(path), 'old_string': 'default!', 'new_string': 'null!', 'replace_all': False}})
    updated = result['updatedInput']
    assert updated['old_string'] == '[Export]\npublic Node Thing = default!;\n'
    assert updated['new_string'] == '[Export, RequiredExport]\npublic Node Thing = null!;\n'
    assert updated['replace_all'] is False
    assert path.read_bytes().count(b'\r\n') == 2


def test_uncorrectable_code_still_blocks():
    result = pe.process({'tool_name': 'Write', 'tool_input': {
        'file_path': 'Scripts/Thing.cs', 'content': 'GD.' + 'Print("x");'}})
    assert result.get('block'), result


if __name__ == '__main__':
    import tempfile
    failures = []
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            try:
                if name in ('test_edit_context_can_fix_attribute_outside_snippet',
                            'test_crlf_edit_matches_live_client_normalization'):
                    with tempfile.TemporaryDirectory() as tmp:
                        fn(Path(tmp))
                else:
                    fn()
                print('ok ' + name)
            except Exception as exc:
                failures.append(name)
                print('FAIL %s: %r' % (name, exc))
    print('%d/8 cases pass' % (8 - len(failures)))
    sys.exit(bool(failures))
