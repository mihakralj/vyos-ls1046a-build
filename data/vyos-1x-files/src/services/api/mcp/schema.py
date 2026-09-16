import json
import logging

from vyos.xml_ref import load_reference, load_op_reference

LOG = logging.getLogger('http_api.mcp.schema')

_conf_xml = None
_op_xml = None

def _get_conf_xml():
    global _conf_xml
    if _conf_xml is None:
        _conf_xml = load_reference()
    return _conf_xml

def _get_op_xml():
    global _op_xml
    if _op_xml is None:
        _op_xml = load_op_reference()
    return _op_xml


def _resolve_conf_ref(path):
    ref = _get_conf_xml().ref
    path_copy = list(path)
    d = ref
    while path_copy and d:
        key = path_copy.pop(0)
        d = d.get(key, {})
        if _is_tag(d) and path_copy:
            path_copy.pop(0)
    return d


def _resolve_op_ref(path):
    ref = _get_op_xml().op_ref
    d = ref
    for key in path:
        if not isinstance(d, dict):
            return {}
        d = d.get(key, {})
    return d


def _is_tag(node_dict):
    return node_dict.get('node_data', {}).get('node_type') == 'tag'


def _is_leaf_node(node_dict):
    return node_dict.get('node_data', {}).get('node_type') == 'leaf'


def _is_multi_node(node_dict):
    return node_dict.get('node_data', {}).get('multi', False)


def _is_valueless_node(node_dict):
    return node_dict.get('node_data', {}).get('valueless', False)


def _get_default_value(node_dict):
    return node_dict.get('node_data', {}).get('default_value')


def _get_conf_children(node_dict):
    if not isinstance(node_dict, dict) or 'node_data' not in node_dict:
        return []
    return sorted(k for k in node_dict if k not in ('node_data', 'component_version'))


def _get_op_children(node_dict):
    if not isinstance(node_dict, dict) or '__node_data' not in node_dict:
        return []
    return sorted(k for k in node_dict if k != '__node_data')


def _project_config_node(node_dict, depth):
    if _is_leaf_node(node_dict):
        if _is_valueless_node(node_dict):
            schema = {'type': 'boolean'}
        elif _is_multi_node(node_dict):
            schema = {'type': 'array', 'items': {'type': 'string'}}
        else:
            schema = {'type': 'string'}
        dv = _get_default_value(node_dict)
        if dv is not None:
            schema['default'] = dv
        return schema

    if _is_tag(node_dict):
        children = _get_conf_children(node_dict)
        child_schemas = {}
        for c in children:
            child_schemas[c] = _project_config_node(node_dict[c], depth - 1 if depth > 0 else 0)
        return {
            'type': 'object',
            'additionalProperties': {
                'type': 'object',
                'properties': child_schemas
            }
        }

    if depth <= 0:
        children = _get_conf_children(node_dict)
        return {'type': 'object', 'x-children': children}

    children = _get_conf_children(node_dict)
    properties = {}
    for c in children:
        properties[c] = _project_config_node(node_dict[c], depth - 1)
    return {'type': 'object', 'properties': properties}


def _project_config_path(path, depth=1):
    if not path:
        ref = _get_conf_xml().ref
        return _project_config_node(ref, depth)
    node_dict = _resolve_conf_ref(path)
    if 'node_data' not in node_dict:
        return {}
    return _project_config_node(node_dict, depth)


def _project_op_node(d, depth):
    if not isinstance(d, dict) or '__node_data' not in d:
        return {'type': 'string'}

    nd = d.get('__node_data', {})
    children = _get_op_children(d)

    if not children:
        if depth > 0:
            return _build_op_leaf_schema(nd)
        return {'type': 'string', 'x-has-dynamic-children': True}

    if depth <= 0:
        return {'type': 'object', 'x-children': children}

    properties = {}
    for c in children:
        properties[c] = _project_op_node(d[c], depth - 1)

    result = {'type': 'object', 'properties': properties}
    help_text = nd.get('help_text', '')
    if help_text:
        result['description'] = help_text
    command = nd.get('command', '')
    if command:
        result['x-command'] = command
    return result


def _build_op_leaf_schema(nd):
    schema = {'type': 'string'}
    help_text = nd.get('help_text', '')
    if help_text:
        schema['description'] = help_text

    constraints = nd.get('constraints')
    if constraints:
        regexes = constraints.get('regexes', [])
        if regexes:
            if len(regexes) == 1:
                schema['pattern'] = regexes[0]
            else:
                schema['x-regexes'] = regexes
        validators = constraints.get('validators', [])
        for v in validators:
            name = v.get('name', '')
            arg = v.get('argument', '')
            if name == 'numeric':
                import re
                m = re.search(r'--range\s+(\d+)-(\d+)', arg or '')
                if m:
                    schema['minimum'] = int(m.group(1))
                    schema['maximum'] = int(m.group(2))

    comp_help = nd.get('comp_help', {})
    enum_list = comp_help.get('list', [])
    if enum_list:
        schema['enum'] = enum_list
    return schema


def _project_op_path(path, depth=1):
    d = _resolve_op_ref(path)
    if not d:
        return {}
    return _project_op_node(d, depth)


def project_config_schema(path=None, depth=1):
    return _project_config_path(path or [], depth)


def project_op_schema(path=None, depth=1):
    return _project_op_path(path or [], depth)


def config_path_valid(path):
    if not path:
        return True
    ref = _get_conf_xml().ref
    path_copy = list(path)
    d = ref
    while path_copy:
        if not isinstance(d, dict) or 'node_data' not in d:
            return False
        key = path_copy.pop(0)
        if _is_tag(d) and path_copy:
            path_copy.pop(0)
            continue
        next_d = d.get(key)
        if next_d is None:
            return True
        d = next_d
    return True


def op_path_valid(path):
    if not path:
        return True

    xml = _get_op_xml()
    d = xml.op_ref
    for key in path:
        if not isinstance(d, dict):
            return False
        next_d = d.get(key)
        if next_d is None:
            return True
        if '__node_data' not in next_d:
            return False
        d = next_d
    return True


def to_json_schema_string(schema):
    return json.dumps(schema, separators=(',', ':'))
