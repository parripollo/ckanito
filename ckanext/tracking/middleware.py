import hashlib
import logging

from urllib.parse import parse_qs


from ckan.common import request
from ckan.types import Response

from ckanext.tracking.model import TrackingRaw


logger = logging.getLogger(__name__)


def track_request(response: Response) -> Response:
    path = request.environ.get("PATH_INFO")
    method = request.environ.get('REQUEST_METHOD')
    if path == '/_tracking' and method == 'POST':
        # wsgi.input is a BytesIO object
        payload = request.environ['wsgi.input'].read().decode(
            'utf-8', errors='replace')
        # The beacon is sent by browsers and bots alike: a trailing '&', a
        # part without '=' or an unencoded '=' in a value used to raise
        # ValueError and turn the request into a 500. parse_qs skips the
        # parts it cannot read.
        data = {
            k: v[0] for k, v in parse_qs(payload, keep_blank_values=True).items()
        }

        # we want a unique anonomized key for each user so that we do
        # not count multiple clicks from the same user.
        key = ''.join([
            request.environ.get('HTTP_USER_AGENT', ''),
            request.environ.get('REMOTE_ADDR', ''),
            request.environ.get('HTTP_ACCEPT_LANGUAGE', ''),
            request.environ.get('HTTP_ACCEPT_ENCODING', ''),
        ])
        # raises a type error on python<3.9
        h = hashlib.new('md5', usedforsecurity=False)
        h.update(key.encode())
        key = h.hexdigest()
        # store key/data here
        try:
            logger.debug(
                "Tracking %s for %s",
                data.get('type'),
                data.get('url'),
            )
            TrackingRaw.create(
                user_key=key,
                url=data.get("url"),
                tracking_type=data.get("type")
            )
        except Exception:
            logger.exception("Error tracking request")

    return response
