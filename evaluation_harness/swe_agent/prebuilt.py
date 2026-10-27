"""Prebuilt SWE-agent images, for running on a VM without general internet egress.

At every container start SWE-agent normally (1) builds a derived image from the task image that adds
a standalone Python with swe-rex (downloading Python source, apt and PyPI packages) and (2) runs each
tool bundle's install.sh, where edit_anthropic pip-installs tree-sitter. Both need the network.

AGENT_DOCKERFILE does all of that ahead of time, on a machine with internet: it is swe-rex 1.4.0's
own build (`DockerDeployment.glibc_dockerfile`) with swe-rex pinned to the host's version and
installed in the builder stage and the libc6 refresh skipped on images without apt, plus the
patched install.sh run the way SWE-agent will run it (in the session swe-rex opens: bash with
/root/.bashrc sourced and PS1 set). At run time install.sh then finds tree-sitter installed and
needs no network. On Python < 3.8, where tree-sitter can't install, it skips instead of aborting.
run_infer.py then points SWE-agent at the prebuilt image with `python_standalone_dir: null`,
which skips the build step.
"""

import base64

SWEREX_VERSION = "1.4.0"
AGENT_IMAGE_PREFIX = "susvibes-agent"

# SWE-agent's edit_anthropic/install.sh as patched by patches/sweagent-v1.1.0-edit-tool-install.patch
# (tests/test_swe_agent_configs.py keeps the two identical). Baked into the image, then rerun by
# SWE-agent at every container start, where it finds tree-sitter installed and does nothing.
TOOL_INSTALL_SCRIPT = """\
# SusVibes: tree-sitter only powers the filemap view of large .py files (str_replace_editor falls
# back to truncated output without it), so a missing install must not abort the run. Skip it when
# already importable (prebuilt images, no network needed) or impossible (0.21.3 needs Python >= 3.8;
# some task images ship Python without pip, which is left as is). Install with `python3 -m pip` so
# it lands in the interpreter the tool runs under.
if python3 -c "import tree_sitter, tree_sitter_languages" 2>/dev/null; then
    :
elif ! python3 -c "import sys; sys.exit(sys.version_info < (3, 8))" 2>/dev/null; then
    echo "Python < 3.8: tree-sitter unavailable; filemap view disabled."
elif ! python3 -m pip --version >/dev/null 2>&1; then
    echo "No pip for python3: tree-sitter unavailable; filemap view disabled."
else
    python3 -m pip install 'tree-sitter==0.21.3' && python3 -m pip install 'tree-sitter-languages' \\
        || echo "tree-sitter install failed; filemap view disabled."
fi
"""
_SCRIPT_B64 = base64.b64encode(TOOL_INSTALL_SCRIPT.encode()).decode()

# The environment SWE-agent's session runs install.sh in: bash with /root/.bashrc sourced, PS1 set.
_IN_SESSION = "export PS1=x; source /root/.bashrc >/dev/null 2>&1; "
_RUN_TOOL_INSTALL = (f"echo {_SCRIPT_B64} | base64 -d > /tmp/susvibes-tool-install.sh && "
                     f"bash -c '{_IN_SESSION}source /tmp/susvibes-tool-install.sh'")

AGENT_DOCKERFILE = f"""\
ARG BASE_IMAGE

FROM python:3.11.9-slim-bookworm AS builder
RUN apt-get update && apt-get install -y wget gcc make zlib1g-dev libssl-dev \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN wget https://www.python.org/ftp/python/3.11.8/Python-3.11.8.tgz && tar xzf Python-3.11.8.tgz
WORKDIR /build/Python-3.11.8
RUN ./configure --prefix=/root/python3.11 --enable-shared LDFLAGS='-Wl,-rpath=/root/python3.11/lib' \\
    && make -j$(nproc) && make install && ldconfig
RUN /root/python3.11/bin/pip3 install --no-cache-dir swe-rex=={SWEREX_VERSION}

FROM $BASE_IMAGE
RUN if command -v apt-get >/dev/null; then \\
        apt-get update && apt-get install -y libc6 && rm -rf /var/lib/apt/lists/*; fi
# No LD_LIBRARY_PATH: the build's rpath finds libpython, and the variable would make a task image's
# own shared-library Python 3.11 load this one's libpython instead of its own.
COPY --from=builder /root/python3.11 /root/python3.11
RUN ln -s /root/python3.11/bin/swerex-remote /usr/local/bin/swerex-remote && swerex-remote --version
RUN {_RUN_TOOL_INSTALL} && rm /tmp/susvibes-tool-install.sh
"""

# Run with `--network none`. Proves swe-rex starts and install.sh completes offline; on Python >= 3.8
# with pip, tree-sitter must import (else install.sh would try PyPI at every start). Prints the session's
# Python version and whether the filemap view is available, for the ship log.
VERIFY_CMD = (
    f"swerex-remote --version >/dev/null && {_RUN_TOOL_INSTALL} >/dev/null && bash -c '{_IN_SESSION}"
    'v=$(python3 -c "import sys; print(sys.version.split()[0])"); '
    'if python3 -c "import tree_sitter, tree_sitter_languages" 2>/dev/null; then f=yes; '
    'elif python3 -c "import sys; sys.exit(sys.version_info < (3, 8))" && python3 -m pip --version >/dev/null 2>&1; '
    'then echo "tree-sitter missing"; exit 1; '
    "else f=no; fi; echo python=$v filemap=$f'"
)


# The session's python3 version, run in the task image itself: the agent image must report the same
# one, or the added layers changed which interpreter the project runs under.
PYTHON_VERSION_CMD = (
    f"bash -c '{_IN_SESSION}"
    'python3 -c "import sys; print(sys.version.split()[0])" 2>/dev/null || echo none'
    "'"
)


def agent_image_name(image_name: str) -> str:
    """Local tag of the prebuilt agent image for a task image (any registry prefix dropped)."""
    repo = image_name.rsplit("/", 1)[-1].split(":", 1)[0]
    return f"{AGENT_IMAGE_PREFIX}/{repo}:swerex-{SWEREX_VERSION}"
