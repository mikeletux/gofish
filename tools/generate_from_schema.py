#!/usr/bin/env python
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import argparse
import io
import logging
import pprint
import textwrap

import jinja2
import requests

LOG = logging.getLogger(__name__)

REDFISH_SCHEMA_BASE = 'http://redfish.dmtf.org/schemas/v1/'
SWORDFISH_SCHEMA_BASE = 'http://redfish.dmtf.org/schemas/swordfish/v1/'

COMMON_NAME_CHANGES = {
    'Oem': 'OEM',
    'Id': 'ID',
}

COMMON_DESC = {
    'Description': 'Description provides a description of this resource.',
    'Id': 'ID uniquely identifies the resource.',
    'Name': 'Name is the name of the resource or array element.',
    '@odata.context': 'ODataContext is the odata context.',
    '@odata.etag': 'ODataEtag is the odata etag.',
    '@odata.id': 'ODataID is the odata identifier.',
    '@odata.type': 'ODataType is the odata type.',
    'Identifier': 'Identifier shall be unique within the managed ecosystem.',
}


def _format_comment(name, description, cutpoint='used', add=' is'):
    if name in COMMON_DESC:
        return '// %s' % COMMON_DESC[name]

    if cutpoint not in description:
        cutpoint = ''

    lines = textwrap.wrap(
        '%s%s %s' % (name, add, description[description.index(cutpoint):]))
    return '\n'.join([('// %s' % l) for l in lines])


def _get_desc(obj):
    desc = obj.get('longDescription')
    if not desc:
        desc = obj.get('description', '')
    return desc


def _get_type(name, obj):
    result = 'string'
    tipe = obj.get('type')
    anyof = obj.get('anyOf') or obj.get('items', {}).get('anyOf')
    if 'count' in name.lower():
        result = 'int'
    elif name == 'Status':
        result = 'common.Status'
    elif name == 'Identifier':
        result = 'common.Identifier'
    elif name == 'Description':
        result = 'string'
    elif tipe == 'object':
        result = name
    elif isinstance(tipe, list):
        for kind in tipe:
            if kind == 'null':
                continue
            if kind == 'integer' or kind == 'number':
                result = 'int'
            elif kind == 'boolean':
                result = 'bool'
            else:
                result = kind
    elif isinstance(anyof, list):
        for kind in anyof:
            if '$ref' in kind:
                result = kind['$ref'].split('/')[-1]
    elif '$ref' in obj.get('items', {}):
        result = obj['items']['$ref'].split('/')[-1]
    elif name[:1] == name[:1].lower() and 'odata' not in name.lower():
        result = 'common.Link'

    if tipe == 'array':
        result = '[]' + result
    
    if 'odata' in name or name in COMMON_NAME_CHANGES:
        result = '%s `json:"%s"`' % (result, name)

    return result


def _add_object(params, name, obj):
    """Adds object information to our template parameters."""
    class_info = {
        'name': name,
        'description': _format_comment(name, _get_desc(obj)),
        'attrs': []}

    for prop in obj.get('properties', []):
        if prop in ['Name', 'Id']:
            continue
        prawp = obj['properties'][prop]
        if prawp.get('deprecated'):
            continue
        attr = {'name': COMMON_NAME_CHANGES.get(prop, prop)}

        if '@odata' in prop:
            props = prop.split('.')
            replacement = 'OData'
            if 'count' in props[-1]:
                replacement = ''
            attr['name'] = '%s%s' % (
                props[0].replace('@odata', replacement), props[-1].title())
        attr['type'] = _get_type(prop, prawp)
        attr['description'] = _format_comment(
            prop, _get_desc(prawp))
        class_info['attrs'].append(attr)
    params['classes'].append(class_info)


def _add_enum(params, name, enum):
    """Adds enum information to our template parameteres."""
    enum_info = {
        'name': name,
        'description': _format_comment(name, _get_desc(enum)),
        'members': []}

    for en in enum.get('enum', []):
        member = {'name': en}
        if enum.get('enumLongDescriptions', {}).get(en):
            desc = enum.get('enumLongDescriptions', {}).get(en)
        else:
            desc = enum.get('enumDescriptions', {}).get(en, '')
        member['description'] = _format_comment(
            '%s%s' % (en, name), desc, cutpoint='shall', add='')
        enum_info['members'].append(member)
    params['enums'].append(enum_info)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'object',
        help='The Swordfish/Redfish schema object to process.')
    parser.add_argument(
        '-t',
        '--type',
        default='swordfish',
        const='swordfish',
        nargs='?',
        choices=['redfish', 'swordfish'],
        help='Define the object type and go package')
    parser.add_argument(
        '-o',
        '--output-file',
        help='File to write results to. Default is to stdout.')
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='Emit verbose output to help debug.')

    args = parser.parse_args()

    if args.type == 'redfish':
        url = '%s%s.json' % (REDFISH_SCHEMA_BASE, args.object)
    elif args.type == 'swordfish':
        url = '%s%s.json' % (SWORDFISH_SCHEMA_BASE, args.object)
    else:
        raise NameError("Unknown schema type")

    LOG.debug(url)

    data = requests.get(url)
    try:
        base_data = data.json()
    except Exception:
        LOG.exception('Error with data:\n%s' % data)
        return

    for classdef in base_data.get('definitions', []):
        if classdef == args.object:
            refs = base_data['definitions'][classdef].get('anyOf', [])
            for ref in refs:
                reflink = ref.get('$ref', '')
                if 'idRef' in reflink:
                    continue
                refurl = reflink.split('#')[0]
                if refurl > url:
                    url = refurl
            break

    object_data = requests.get(url).json()
    params = {'object_name': args.object, 'classes': [], 'enums': [], 'package': args.type}

    for name in object_data['definitions']:
        if name == 'Actions':
            continue
        definition = object_data['definitions'][name]
        if definition.get('type') == 'object':
            properties = definition.get('properties', '')
            if not ('target' in properties and 'title' in properties):
                _add_object(params, name, definition)
        elif definition.get('enum'):
            _add_enum(params, name, definition)
        else:
            LOG.debug('Skipping %s', definition)

    with io.open('source.tmpl', 'r', encoding='utf-8') as f:
        template_body = f.read()

    template = jinja2.Template(template_body)
    print(template.render(**params))


if __name__ == '__main__':
    main()
