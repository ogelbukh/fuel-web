#!/bin/bash

#    Copyright 2016 Mirantis, Inc.
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

set -eu

# settings
ROOT=$(dirname `readlink -f $0`)
NAILGUN_ROOT=$ROOT/nailgun
TESTRTESTS="nosetests"
FLAKE8="flake8"
PEP8="pep8"
GULP="./node_modules/.bin/gulp"
TOXENV=${TOXENV:-py27}
LAST_VERSION="8.0"
FIXTURE="nailgun/nailgun/fixtures/openstack.yaml"

# test options
testrargs=
testropts="--with-timer --timer-warning=10 --timer-ok=2 --timer-top-n=10"

# nosetest xunit options
NAILGUN_XUNIT=${NAILGUN_XUNIT:-"$ROOT/nailgun.xml"}
EXTENSIONS_XUNIT=${EXTENSIONS_XUNIT:-"$ROOT/extensions.xml"}
NAILGUN_PORT=${NAILGUN_PORT:-5544}
TEST_NAILGUN_DB=${TEST_NAILGUN_DB:-nailgun}
NAILGUN_CHECK_PATH=${NAILGUN_CHECK_PATH:-"/api/version"}
NAILGUN_STARTUP_TIMEOUT=${NAILGUN_STARTUP_TIMEOUT:-10}
NAILGUN_SHUTDOWN_TIMEOUT=${NAILGUN_SHUTDOWN_TIMEOUT:-3}
ARTIFACTS=${ARTIFACTS:-`pwd`/test_run}
TEST_WORKERS=${TEST_WORKERS:-0}
mkdir -p $ARTIFACTS

function run_cleanup {
  find . -type f -name "*.pyc" -delete
  rm -f *.log
  rm -f *.pid
}

function prepare_artifacts {
  local artifacts=$1
  local config=$2
  mkdir -p $artifacts
  create_settings_yaml $config $artifacts
}

function create_settings_yaml {
  local config_path=$1
  local artifacts_path=$2
  cat > $config_path <<EOL
DEVELOPMENT: 1
STATIC_DIR: ${artifacts_path}/static_compressed
TEMPLATE_DIR: ${artifacts_path}/static_compressed
DATABASE:
  name: ${TEST_NAILGUN_DB}
  engine: "postgresql"
  host: "localhost"
  port: "5432"
  user: "nailgun"
  passwd: "nailgun"
API_LOG: ${artifacts_path}/api.log
APP_LOG: ${artifacts_path}/app.log
EOL
}

# Arguments:
#
#   $1 -- insert default data into database if true
function syncdb {
  pushd $ROOT/nailgun >> /dev/null
  local config=$1
  local defaults=$2
  NAILGUN_CONFIG=$config tox -evenv -- python manage.py syncdb > /dev/null

  if [[ $# -ne 0 && $defaults = true ]]; then
    NAILGUN_CONFIG=$config tox -evenv -- python manage.py loaddefault > /dev/null
    NAILGUN_CONFIG=$config tox -evenv -- python manage.py loaddata nailgun/fixtures/sample_environment.json > /dev/null
  fi

  popd >> /dev/null
}

function dropdb {
  pushd $ROOT/nailgun >> /dev/null
  local config=$1
  NAILGUN_CONFIG=$config tox -evenv -- python manage.py dropdb > /dev/null

  popd >> /dev/null
}

function dumpdb {
  pushd $ROOT/nailgun >> /dev/null
  local config=$1
  local sha=$2
  NAILGUN_CONFIG=$config tox -evenv -- python manage.py dumpdata release | \
    tail -n +8 > $ROOT/openstack_${sha}.yaml

  popd >> /dev/null
}

function main {
  local COMMIT=$1
  local artifacts=${ARTIFACTS}/nailgun
  local config=${artifacts}/test.yaml  
  prepare_artifacts $artifacts $config

  dropdb $config
  
  git checkout origin/master
  echo "Syncing DB for origin/master version"
  NAILGUN_CONFIG=$config tox -evenv -- python manage.py syncdb > /dev/null
  echo "Uploading fixtures for version ${LAST_VERSION}"
  NAILGUN_CONFIG=$config tox -evenv -- python manage.py loaddefault > /dev/null

  dumpdb $config master
  
  git checkout $COMMIT
  echo "Running migrations for patch set"
  NAILGUN_CONFIG=$config tox -evenv -- python manage.py migrate upgrade head > /dev/null
  
  dumpdb $config HEAD

}

main $@
