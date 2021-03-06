# Copyright 2012 OpenStack, Foundation
# All Rights Reserved.
#
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

import copy
import functools

from glance.common import exception
import glance.openstack.common.log as logging
from glance.openstack.common import timeutils
from glance.openstack.common import uuidutils


LOG = logging.getLogger(__name__)

DATA = {
    'images': {},
    'members': {},
    'tags': {},
    'locations': [],
}


def log_call(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        LOG.info(_('Calling %(funcname)s: args=%(args)s, kwargs=%(kwargs)s') %
                 {"funcname": func.__name__,
                  "args": args,
                  "kwargs": kwargs})
        output = func(*args, **kwargs)
        LOG.info(_('Returning %(funcname)s: %(output)s') %
                 {"funcname": func.__name__,
                  "output": output})
        return output
    return wrapped


def reset():
    global DATA
    DATA = {
        'images': {},
        'members': [],
        'tags': {},
        'locations': [],
    }


def setup_db_env(*args, **kwargs):
    """
    Setup global environment configuration variables.

    We have no connection-oriented environment variables, so this is a NOOP.
    """
    pass


def clear_db_env(*args, **kwargs):
    """
    Setup global environment configuration variables.

    We have no connection-oriented environment variables, so this is a NOOP.
    """
    pass


def _get_session():
    return DATA


def _image_locations_format(image_id, value, meta_data):
    dt = timeutils.utcnow()
    return {
        'id': uuidutils.generate_uuid(),
        'image_id': image_id,
        'created_at': dt,
        'updated_at': dt,
        'deleted_at': None,
        'deleted': False,
        'url': value,
        'metadata': meta_data,
    }


def _image_property_format(image_id, name, value):
    return {
        'image_id': image_id,
        'name': name,
        'value': value,
        'deleted': False,
        'deleted_at': None,
    }


def _image_member_format(image_id, tenant_id, can_share, status='pending'):
    dt = timeutils.utcnow()
    return {
        'id': uuidutils.generate_uuid(),
        'image_id': image_id,
        'member': tenant_id,
        'can_share': can_share,
        'status': status,
        'created_at': dt,
        'updated_at': dt,
    }


def _image_format(image_id, **values):
    dt = timeutils.utcnow()
    image = {
        'id': image_id,
        'name': None,
        'owner': None,
        'locations': [],
        'status': 'queued',
        'protected': False,
        'is_public': False,
        'container_format': None,
        'disk_format': None,
        'min_ram': 0,
        'min_disk': 0,
        'size': None,
        'checksum': None,
        'tags': [],
        'created_at': dt,
        'updated_at': dt,
        'deleted_at': None,
        'deleted': False,
    }

    locations = values.pop('locations', None)
    if locations is not None:
        locations = [
            _image_locations_format(image_id, location['url'],
                                    location['metadata'])
            for location in locations
        ]
        image['locations'] = locations

    #NOTE(bcwaldon): store properties as a list to match sqlalchemy driver
    properties = values.pop('properties', {})
    properties = [{'name': k,
                   'value': v,
                   'image_id': image_id,
                   'deleted': False} for k, v in properties.items()]
    image['properties'] = properties

    image.update(values)
    return image


def _filter_images(images, filters, context,
                   status='accepted', is_public=None,
                   admin_as_user=False):
    filtered_images = []
    if 'properties' in filters:
        prop_filter = filters.pop('properties')
        filters.update(prop_filter)

    if status == 'all':
        status = None

    visibility = filters.pop('visibility', None)

    for image in images:
        member = image_member_find(context, image_id=image['id'],
                                   member=context.owner, status=status)
        is_member = len(member) > 0
        has_ownership = context.owner and image['owner'] == context.owner
        can_see = (image['is_public'] or has_ownership or is_member or
                   (context.is_admin and not admin_as_user))
        if not can_see:
            continue

        if visibility:
            if visibility == 'public':
                if not image['is_public']:
                    continue
            elif visibility == 'private':
                if image['is_public']:
                    continue
                if not (has_ownership or (context.is_admin
                        and not admin_as_user)):
                    continue
            elif visibility == 'shared':
                if not is_member:
                    continue

        if is_public is not None:
            if not image['is_public'] == is_public:
                continue

        add = True
        for k, value in filters.iteritems():
            key = k
            if k.endswith('_min') or k.endswith('_max'):
                key = key[0:-4]
                try:
                    value = int(value)
                except ValueError:
                    msg = _("Unable to filter on a range "
                            "with a non-numeric value.")
                    raise exception.InvalidFilterRangeValue(msg)
            if k.endswith('_min'):
                add = image.get(key) >= value
            elif k.endswith('_max'):
                add = image.get(key) <= value
            elif k != 'is_public' and image.get(k) is not None:
                add = image.get(key) == value
            elif k == 'tags':
                filter_tags = value
                image_tags = image_tag_get_all(context, image['id'])
                for tag in filter_tags:
                    if tag not in image_tags:
                        add = False
                        break
            else:
                properties = {}
                for p in image['properties']:
                    properties = {p['name']: p['value'],
                                  'deleted': p['deleted']}
                add = (properties.get(key) == value and
                       properties.get('deleted') is False)
            if not add:
                break

        if add:
            filtered_images.append(image)

    return filtered_images


def _do_pagination(context, images, marker, limit, show_deleted,
                   status='accepted'):
    start = 0
    end = -1
    if marker is None:
        start = 0
    else:
        # Check that the image is accessible
        _image_get(context, marker, force_show_deleted=show_deleted,
                   status=status)

        for i, image in enumerate(images):
            if image['id'] == marker:
                start = i + 1
                break
        else:
            raise exception.NotFound()

    end = start + limit if limit is not None else None
    return images[start:end]


def _sort_images(images, sort_key, sort_dir):
    reverse = False
    if images and not (sort_key in images[0]):
        raise exception.InvalidSortKey()
    keyfn = lambda x: (x[sort_key] if x[sort_key] is not None else '',
                       x['created_at'], x['id'])
    reverse = sort_dir == 'desc'
    images.sort(key=keyfn, reverse=reverse)

    return images


def _image_get(context, image_id, force_show_deleted=False, status=None):
    try:
        image = DATA['images'][image_id]
        image['locations'] = _image_location_get_all(image_id)

    except KeyError:
        LOG.info(_('Could not find image %s') % image_id)
        raise exception.NotFound()

    if image['deleted'] and not (force_show_deleted or context.show_deleted):
        LOG.info(_('Unable to get deleted image'))
        raise exception.NotFound()

    if not is_image_visible(context, image):
        LOG.info(_('Unable to get unowned image'))
        raise exception.Forbidden("Image not visible to you")

    return image


@log_call
def image_get(context, image_id, session=None, force_show_deleted=False):
    image = _image_get(context, image_id, force_show_deleted)
    image = _normalize_locations(image)
    return copy.deepcopy(image)


@log_call
def image_get_all(context, filters=None, marker=None, limit=None,
                  sort_key='created_at', sort_dir='desc',
                  member_status='accepted', is_public=None,
                  admin_as_user=False):
    filters = filters or {}
    images = DATA['images'].values()
    images = _filter_images(images, filters, context, member_status,
                            is_public, admin_as_user)
    images = _sort_images(images, sort_key, sort_dir)
    images = _do_pagination(context, images, marker, limit,
                            filters.get('deleted'))

    for image in images:
        image['locations'] = _image_location_get_all(image['id'])
        _normalize_locations(image)

    return images


@log_call
def image_property_create(context, values):
    image = _image_get(context, values['image_id'])
    prop = _image_property_format(values['image_id'],
                                  values['name'],
                                  values['value'])
    image['properties'].append(prop)
    return prop


@log_call
def image_property_delete(context, prop_ref, image_ref, session=None):
    prop = None
    for p in DATA['images'][image_ref]['properties']:
        if p['name'] == prop_ref:
            prop = p
    if not prop:
        raise exception.NotFound()
    prop['deleted_at'] = timeutils.utcnow()
    prop['deleted'] = True
    return prop


@log_call
def image_member_find(context, image_id=None, member=None, status=None):
    filters = []
    images = DATA['images']
    members = DATA['members']

    def is_visible(member):
        return (member['member'] == context.owner or
                images[member['image_id']]['owner'] == context.owner)

    if not context.is_admin:
        filters.append(is_visible)

    if image_id is not None:
        filters.append(lambda m: m['image_id'] == image_id)
    if member is not None:
        filters.append(lambda m: m['member'] == member)
    if status is not None:
        filters.append(lambda m: m['status'] == status)

    for f in filters:
        members = filter(f, members)
    return [copy.deepcopy(member) for member in members]


@log_call
def image_member_create(context, values):
    member = _image_member_format(values['image_id'],
                                  values['member'],
                                  values.get('can_share', False),
                                  values.get('status', 'pending'))
    global DATA
    DATA['members'].append(member)
    return copy.deepcopy(member)


@log_call
def image_member_update(context, member_id, values):
    global DATA
    for member in DATA['members']:
        if (member['id'] == member_id):
            member.update(values)
            member['updated_at'] = timeutils.utcnow()
            return copy.deepcopy(member)
    else:
        raise exception.NotFound()


@log_call
def image_member_delete(context, member_id):
    global DATA
    for i, member in enumerate(DATA['members']):
        if (member['id'] == member_id):
            del DATA['members'][i]
            break
    else:
        raise exception.NotFound()


def _image_locations_set(image_id, locations):
    global DATA
    image = DATA['images'][image_id]
    for location in image['locations']:
        location['deleted'] = True
        location['deleted_at'] = timeutils.utcnow()

    for i, location in enumerate(DATA['locations']):
        if image_id == location['image_id'] and location['deleted'] is False:
            del DATA['locations'][i]

    for location in locations:
        location_ref = _image_locations_format(image_id, value=location['url'],
                                               meta_data=location['metadata'])
        DATA['locations'].append(location_ref)

        image['locations'].append(location_ref)


def _normalize_locations(image):
    undeleted_locations = filter(lambda x: not x['deleted'],
                                 image['locations'])
    image['locations'] = [{'url': loc['url'],
                           'metadata': loc['metadata']}
                          for loc in undeleted_locations]
    return image


def _image_location_get_all(image_id):
    location_data = []
    for location in DATA['locations']:
        if image_id == location['image_id']:
            location_data.append(location)
    return location_data


@log_call
def image_create(context, image_values):
    global DATA
    image_id = image_values.get('id', uuidutils.generate_uuid())

    if image_id in DATA['images']:
        raise exception.Duplicate()

    if 'status' not in image_values:
        raise exception.Invalid('status is a required attribute')

    allowed_keys = set(['id', 'name', 'status', 'min_ram', 'min_disk', 'size',
                        'checksum', 'locations', 'owner', 'protected',
                        'is_public', 'container_format', 'disk_format',
                        'created_at', 'updated_at', 'deleted_at', 'deleted',
                        'properties', 'tags'])

    incorrect_keys = set(image_values.keys()) - allowed_keys
    if incorrect_keys:
        raise exception.Invalid(
            'The keys %s are not valid' % str(incorrect_keys))

    image = _image_format(image_id, **image_values)
    DATA['images'][image_id] = image

    location_data = image_values.get('locations', None)
    if location_data is not None:
        _image_locations_set(image_id, location_data)

    DATA['tags'][image_id] = image.pop('tags', [])

    return _normalize_locations(copy.deepcopy(image))


@log_call
def image_update(context, image_id, image_values, purge_props=False):
    global DATA
    try:
        image = DATA['images'][image_id]
    except KeyError:
        raise exception.NotFound(image_id=image_id)

    location_data = image_values.pop('locations', None)
    if location_data is not None:
        _image_locations_set(image_id, location_data)

    # replace values for properties that already exist
    new_properties = image_values.pop('properties', {})
    for prop in image['properties']:
        if prop['name'] in new_properties:
            prop['value'] = new_properties.pop(prop['name'])
        elif purge_props:
            # this matches weirdness in the sqlalchemy api
            prop['deleted'] = True

    # add in any completly new properties
    image['properties'].extend([{'name': k, 'value': v,
                                 'image_id': image_id, 'deleted': False}
                                for k, v in new_properties.items()])

    image['updated_at'] = timeutils.utcnow()
    image.update(image_values)
    DATA['images'][image_id] = image
    return _normalize_locations(image)


@log_call
def image_destroy(context, image_id):
    global DATA
    try:
        DATA['images'][image_id]['deleted'] = True
        DATA['images'][image_id]['deleted_at'] = timeutils.utcnow()

        # NOTE(flaper87): Move the image to one of the deleted statuses
        # if it hasn't been done yet.
        if (DATA['images'][image_id]['status'] not in
                ['deleted', 'pending_delete']):
            DATA['images'][image_id]['status'] = 'deleted'

        _image_locations_set(image_id, [])

        for prop in DATA['images'][image_id]['properties']:
            image_property_delete(context, prop['name'], image_id)

        members = image_member_find(context, image_id=image_id)
        for member in members:
            image_member_delete(context, member['id'])

        tags = image_tag_get_all(context, image_id)
        for tag in tags:
            image_tag_delete(context, image_id, tag)

        _normalize_locations(DATA['images'][image_id])

        return copy.deepcopy(DATA['images'][image_id])
    except KeyError:
        raise exception.NotFound()


@log_call
def image_tag_get_all(context, image_id):
    return DATA['tags'].get(image_id, [])


@log_call
def image_tag_get(context, image_id, value):
    tags = image_tag_get_all(context, image_id)
    if value in tags:
        return value
    else:
        raise exception.NotFound()


@log_call
def image_tag_set_all(context, image_id, values):
    global DATA
    DATA['tags'][image_id] = values


@log_call
def image_tag_create(context, image_id, value):
    global DATA
    DATA['tags'][image_id].append(value)
    return value


@log_call
def image_tag_delete(context, image_id, value):
    global DATA
    try:
        DATA['tags'][image_id].remove(value)
    except ValueError:
        raise exception.NotFound()


def is_image_mutable(context, image):
    """Return True if the image is mutable in this context."""
    # Is admin == image mutable
    if context.is_admin:
        return True

    # No owner == image not mutable
    if image['owner'] is None or context.owner is None:
        return False

    # Image only mutable by its owner
    return image['owner'] == context.owner


def is_image_sharable(context, image, **kwargs):
    """Return True if the image can be shared to others in this context."""
    # Is admin == image sharable
    if context.is_admin:
        return True

    # Only allow sharing if we have an owner
    if context.owner is None:
        return False

    # If we own the image, we can share it
    if context.owner == image['owner']:
        return True

    # Let's get the membership association
    if 'membership' in kwargs:
        member = kwargs['membership']
        if member is None:
            # Not shared with us anyway
            return False
    else:
        members = image_member_find(context,
                                    image_id=image['id'],
                                    member=context.owner)
        if members:
            member = members[0]
        else:
            # Not shared with us anyway
            return False

    # It's the can_share attribute we're now interested in
    return member['can_share']


def is_image_visible(context, image, status=None):
    """Return True if the image is visible in this context."""
    # Is admin == image visible
    if context.is_admin:
        return True

    # No owner == image visible
    if image['owner'] is None:
        return True

    # Image is_public == image visible
    if image['is_public']:
        return True

    # Perform tests based on whether we have an owner
    if context.owner is not None:
        if context.owner == image['owner']:
            return True

        # Figure out if this image is shared with that tenant
        if status == 'all':
            status = None
        members = image_member_find(context,
                                    image_id=image['id'],
                                    member=context.owner,
                                    status=status)
        if members:
            return True

    # Private image
    return False


def user_get_storage_usage(context, owner_id, image_id=None, session=None):
    images = image_get_all(context, filters={'owner': owner_id})
    total = 0
    for image in images:
        if image['status'] in ['killed', 'pending_delete', 'deleted']:
            continue

        if image['id'] != image_id:
            locations = [l for l in image['locations']
                         if not l.get('deleted', False)]
            total += (image['size'] * len(locations))
    return total
