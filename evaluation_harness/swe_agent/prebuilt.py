"""Prebuilt SWE-agent images, for running on a VM without general internet egress.

At every container start SWE-agent normally (1) builds a derived image from the task image that adds
a standalone Python with swe-rex (downloading Python source, apt and PyPI packages) and (2) runs each
tool bundle's install.sh, where edit_anthropic pip-installs tree-sitter. Both need the network.

AGENT_DOCKERFILE does all of that ahead of time, on a machine with internet: it is swe-rex 1.4.0's
own build (`DockerDeployment.glibc_dockerfile`) with swe-rex pinned to the host's version and
installed in the builder stage, plus the tree-sitter packages installed the way install.sh will look
for them at run time (in the session swe-rex opens: bash with /root/.bashrc sourced and PS1 set).
With the packages already present, install.sh's pip calls are no-ops that need no network.
run_infer.py then points SWE-agent at the prebuilt image with `python_standalone_dir: null`,
which skips the build step.
"""

SWEREX_VERSION = "1.4.0"
AGENT_IMAGE_PREFIX = "susvibes-agent"

# Same commands, in the same environment, that SWE-agent's edit_anthropic/install.sh runs.
TOOL_INSTALL_CMD = (
    "export PS1=x; source /root/.bashrc >/dev/null 2>&1; "
    "pip install 'tree-sitter==0.21.3' && pip install 'tree-sitter-languages'"
)

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
RUN apt-get update && apt-get install -y libc6 && rm -rf /var/lib/apt/lists/*
COPY --from=builder /root/python3.11 /root/python3.11
ENV LD_LIBRARY_PATH=/root/python3.11/lib:${{LD_LIBRARY_PATH:-}}
RUN ln -s /root/python3.11/bin/swerex-remote /usr/local/bin/swerex-remote && swerex-remote --version
RUN bash -c "{TOOL_INSTALL_CMD}"
"""

# Run with `--network none`: proves the image starts swe-rex and passes install.sh offline.
VERIFY_CMD = f'swerex-remote --version && bash -c "{TOOL_INSTALL_CMD}"'


def agent_image_name(image_name: str) -> str:
    """Local tag of the prebuilt agent image for a task image (any registry prefix dropped)."""
    repo = image_name.rsplit("/", 1)[-1].split(":", 1)[0]
    return f"{AGENT_IMAGE_PREFIX}/{repo}:swerex-{SWEREX_VERSION}"
