# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import logging
import time
from typing import Dict, Any, Optional

import requests

from airflow.exceptions import AirflowException
from airflow.models import BaseOperator
from airflow.providers.plexus.hooks.plexus import PlexusHook
from airflow.utils.decorators import apply_defaults

logger = logging.getLogger(__name__)


class PlexusJobOperator(BaseOperator):
    """
    Submits a Plexus job.

    :param job_params: parameters required to launch a job.
    :type job_params: dict

    Required job parameters are the following
        - "name": job name created by user.
        - "app": name of the application to run. found in Plexus UI.
        - "queue": public cluster name. found in Plexus UI.
        - "num_nodes": number of nodes.
        - "num_cores":  number of cores per node.

    """

    @apply_defaults
    def __init__(self, job_params: Dict, **kwargs) -> None:
        super().__init__(**kwargs)

        self.job_params = job_params
        self.required_params = set(["name", "app", "queue", "num_cores", "num_nodes"])
        self.lookups = {
            "app": ("apps/", "id", "name"),
            "billing_account_id": ("users/{}/billingaccounts/", "id", None),
            "queue": ("queues/", "id", "public_name"),
        }
        self.job_params.update({"billing_account_id": None})
        self.is_service = None

    def execute(self, context: Any) -> Any:
        hook = PlexusHook()
        params = self.construct_job_params(hook)
        if self.is_service is True:
            if self.job_params.get("expected_runtime") is None:
                end_state = "Running"
            else:
                end_state = "Finished"
        elif self.is_service is False:
            end_state = "Completed"
        else:
            raise AirflowException(
                "Unable to determine if application "
                "is running as a batch job or service. "
                "Contact Core Scientific AI Team."
            )
        logger.info("creating job w/ following params: %s", params)
        jobs_endpoint = hook.host + "jobs/"
        headers = {"Authorization": "Bearer {}".format(hook.token)}
        create_job = requests.post(jobs_endpoint, headers=headers, data=params, timeout=5)
        if create_job.ok:
            job = create_job.json()
            jid = job["id"]
            state = job["last_state"]
            while state != end_state:
                time.sleep(3)
                jid_endpoint = jobs_endpoint + "{}/".format(jid)
                get_job = requests.get(jid_endpoint, headers=headers, timeout=5)
                if not get_job.ok:
                    raise AirflowException(
                        "Could not retrieve job status. Status Code: [{0}]. "
                        "Reason: {1} - {2}".format(get_job.status_code, get_job.reason, get_job.text)
                    )
                new_state = get_job.json()["last_state"]
                if new_state in ("Cancelled", "Failed"):
                    raise AirflowException("Job {}".format(new_state))
                elif new_state != state:
                    logger.info("job is %s", new_state)
                state = new_state
        else:
            raise AirflowException(
                "Could not start job. Status Code: [{}]. "
                "Reason: {} - {}".format(create_job.status_code, create_job.reason, create_job.text)
            )

    def _api_lookup(self, param: str, hook):
        lookup = self.lookups[param]
        key = lookup[1]
        mapping = None if lookup[2] is None else (lookup[2], self.job_params[param])

        if param == "billing_account_id":
            endpoint = hook.host + lookup[0].format(hook.user_id)
        else:
            endpoint = hook.host + lookup[0]
        headers = {"Authorization": "Bearer {}".format(hook.token)}
        response = requests.get(endpoint, headers=headers, timeout=5)
        results = response.json()["results"]

        v = None
        if mapping is None:
            v = results[0][key]
        else:
            for dct in results:
                if dct[mapping[0]] == mapping[1]:
                    v = dct[key]
                if param == 'app':
                    self.is_service = dct['is_service']
        if v is None:
            raise AirflowException(
                "Could not locate value for param:{} at endpoint: {}".format(key, endpoint)
            )

        return v

    def construct_job_params(self, hook: Any) -> Dict[Any, Optional[Any]]:
        """
        Creates job_params dict for api call to launch a Plexus job.

        Some parameters required to launch a job
        are not available to the user in the Plexus
        UI. For example, an app id is required, but
        only the app name is provided in the UI.
        This function acts as a backend lookup
        of the required param value using the
        user-provided value.

        :param hook: plexus hook object
        :type hook: airflow hook
        """
        missing_params = self.required_params - set(self.job_params)
        if len(missing_params) > 0:
            raise AirflowException(
                "Missing the following required job_params: {}".format(", ".join(missing_params))
            )
        params = {}
        for prm in self.job_params:
            if prm in self.lookups:
                v = self._api_lookup(param=prm, hook=hook)
                params[prm] = v
            else:
                params[prm] = self.job_params[prm]
        return params
