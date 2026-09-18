'''
 Factory for loading system prompts from different locations, Local disk or S3, etc.
 This is slow for doing during pipeline execution.
 We should load the system prompt once and store it in memory (context).
 Disk access is used for local development and testing.
 S3 access is used for production.
'''


from . import prompt_load_local_disk
from . import prompt_load_s3
import logging

logger = logging.getLogger(__name__)

load_map = {

    "local_disk" : prompt_load_local_disk,
    "s3": prompt_load_s3
}

def get_prompt(location: dict[str, str]):

    type_resource = location.get("resource").strip().lower()
    uri = location.get("uri").strip().lower()

    loader = load_map.get(type_resource)

    return loader.get_prompt(uri)