import requests
from jinja2 import Environment
from os import getenv
from nornir import InitNornir
from nornir_utils.plugins.functions import print_result
from nornir_jinja2.plugins.tasks import template_file
from nornir_napalm.plugins.tasks import napalm_get, napalm_configure
import re
import logging

requests.packages.urllib3.disable_warnings()

nautobotServer = getenv("NAUTOBOT_SERVER")
token = getenv("NAUTOBOT_TOKEN")

nr = InitNornir(
    inventory={
        "plugin": "NautobotInventory",
        "options": {
            "nautobot_url": f"https://{nautobotServer}",
            "nautobot_token": token,
            "filter_parameters": {"manufacturer": "arista"},
            "ssl_verify": False,
        },
    }
)
nr.inventory.defaults.username = getenv("DEVICE_USERNAME")
nr.inventory.defaults.password = getenv("DEVICE_PASSWORD")


def get_device_data(hostname):
    nautobotHeaders = {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    query = """
    query ($device_name: [String]) {
    devices (name: $device_name) {
        interfaces {
        name
        enabled
        lag {
            name
        }
        description
        tagged_vlans {
            vid
        }
        untagged_vlan {
            vid
        }
        ip_addresses {
            vrf {
            name
            }
            address
        }
        }
    }
    }
    """
    queryDict = {"query": query, "variables": {"device_name": hostname}}

    graphQLquery = requests.post(
        url=f"https://{nautobotServer}/api/graphql/",
        json=queryDict,
        headers=nautobotHeaders,
        verify=False,
    ).json()

    data = graphQLquery["data"]["devices"][0]
    return data


def generate_config(task):
    task.host["facts"] = get_device_data(
        hostname=task.host.data["pynautobot_dictionary"]["name"]
    )

    manufacturer = task.host.data["pynautobot_dictionary"]["device_type"][
        "manufacturer"
    ]["slug"]

    jinjaEnvironment = Environment(lstrip_blocks=True, trim_blocks=True)
    result = task.run(
        task=template_file,
        path="./",
        template=f"{manufacturer}.j2",
        jinja_env=jinjaEnvironment,
        severity_level=logging.DEBUG,
    )
    return result.result


def build_new_config(task):
    getConfig = task.run(
        task=napalm_get,
        getters=["config"],
        severity_level=logging.DEBUG,
    )
    getConfigResult = getConfig.result
    runningConfig = getConfigResult["config"]["running"]

    interfacePattern = re.compile(f"^interface ([^!]+\n)", flags=re.I | re.M)

    newInterfaceConfig = generate_config(task)
    newConfig = re.sub(interfacePattern, newInterfaceConfig, runningConfig)

    return newConfig


def replace_config(task, dry_run=False):
    replaceConfig = build_new_config(task)
    task.run(
        napalm_configure, configuration=replaceConfig, replace=True, dry_run=dry_run
    )


def main():
    results = nr.run(task=replace_config, dry_run=True, name="PRINTING DRY RUN DIFF")
    print_result(results)

    commit = input("Commit[Y/n]:").upper()
    if commit == "Y":
        nr.run(task=replace_config, name="PUSHING DEVICE CONFIGURATIONS")


if __name__ == "__main__":
    main()
