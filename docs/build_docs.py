#!/usr/bin/env python
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
import argparse
import ast
import os
import re
import shlex
import shutil
import sys
from contextlib import suppress
from functools import total_ordering
from glob import glob
from itertools import chain
from subprocess import run
from tempfile import NamedTemporaryFile
from typing import Iterable, List, NamedTuple, Optional, Set

if __name__ != "__main__":
    raise Exception(
        "This file is intended to be executed as an executable program. You cannot use it as a module."
        "To run this script, run the ./build_docs.py command"
    )


@total_ordering
class DocBuildError(NamedTuple):
    """
    Errors found in docs build.
    """

    file_path: Optional[str]
    line_no: Optional[int]
    message: str

    def __eq__(self, other):
        left = (self.file_path, self.line_no, self.message)
        right = (other.file_path, other.line_no, other.message)
        return left == right

    def __ne__(self, other):
        return not self == other

    def __lt__(self, other):
        file_path_a = self.file_path or ''
        file_path_b = other.file_path or ''
        line_no_a = self.line_no or 0
        line_no_b = other.line_no or 0
        return (file_path_a, line_no_a, self.message) < (file_path_b, line_no_b, other.message)


@total_ordering
class SpellingError(NamedTuple):
    """
    Spelling errors found when building docs.
    """

    file_path: Optional[str]
    line_no: Optional[int]
    spelling: Optional[str]
    suggestion: Optional[str]
    context_line: Optional[str]
    message: str

    def __eq__(self, other):
        left = (self.file_path, self.line_no, self.spelling, self.context_line, self.message)
        right = (other.file_path, other.line_no, other.spelling, other.context_line, other.message)
        return left == right

    def __ne__(self, other):
        return not self == other

    def __lt__(self, other):
        file_path_a = self.file_path or ''
        file_path_b = other.file_path or ''
        line_no_a = self.line_no or 0
        line_no_b = other.line_no or 0
        context_line_a = self.context_line or ''
        context_line_b = other.context_line or ''
        return (file_path_a, line_no_a, context_line_a, self.spelling, self.message) < \
               (file_path_b, line_no_b, context_line_b, other.spelling, other.message)


build_errors: List[DocBuildError] = []
spelling_errors: List[SpellingError] = []

ROOT_PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), os.pardir))
ROOT_PACKAGE_DIR = os.path.join(ROOT_PROJECT_DIR, "airflow")
DOCS_DIR = os.path.join(ROOT_PROJECT_DIR, "docs")

_API_DIR = os.path.join(DOCS_DIR, "_api")
_BUILD_DIR = os.path.join(DOCS_DIR, "_build")


def clean_files() -> None:
    """
    Cleanup all artifacts generated by previous builds.
    """
    shutil.rmtree(_API_DIR, ignore_errors=True)
    shutil.rmtree(_BUILD_DIR, ignore_errors=True)
    os.makedirs(_API_DIR, exist_ok=True)
    os.makedirs(_BUILD_DIR, exist_ok=True)
    print(f"Recreated content of the ${_BUILD_DIR} and ${_API_DIR} folders")


def display_errors_summary() -> None:
    """
    Displays summary of errors
    """
    for warning_no, error in enumerate(sorted(build_errors), 1):
        print("=" * 20, f"Error {warning_no:3}", "=" * 20)
        print(error.message)
        print()
        if error.file_path and error.line_no:
            print(f"File path: {error.file_path} ({error.line_no})")
            print()
            print(prepare_code_snippet(error.file_path, error.line_no))
        elif error.file_path:
            print(f"File path: {error.file_path}")

    print("=" * 50)


def display_spelling_error_summary() -> None:
    """
    Displays summary of Spelling errors
    """
    for warning_no, error in enumerate(sorted(spelling_errors), 1):
        print("=" * 20, f"Error {warning_no:3}", "=" * 20)
        print(error.message)
        print()
        if error.file_path:
            print(f"File path: {error.file_path}")
            if error.spelling:
                print(f"Incorrect Spelling: '{error.spelling}'")
            if error.suggestion:
                print(f"Suggested Spelling: '{error.suggestion}'")
            if error.context_line:
                print(f"Line with Error: '{error.context_line}'")
            if error.line_no:
                print(f"Line Number: {error.line_no}")
                print(prepare_code_snippet(os.path.join(DOCS_DIR, error.file_path), error.line_no))

    print("=" * 50)
    print()
    msg = """
If the spelling is correct, add the spelling to docs/spelling_wordlist.txt
or use the spelling directive.
Check https://sphinxcontrib-spelling.readthedocs.io/en/latest/customize.html#private-dictionaries
for more details.
    """
    print(msg)
    print()


def find_existing_guide_operator_names() -> Set[str]:
    """
    Find names of existing operators.

    :return names of existing operators.
    """
    operator_names = set()

    paths = glob(f"${DOCS_DIR}/howto/operator/**/*.rst", recursive=True)
    for path in paths:
        with open(path) as f:
            operator_names |= set(re.findall(".. _howto/operator:(.+?):", f.read()))

    return operator_names


def extract_ast_class_def_by_name(ast_tree, class_name):
    """
    Extracts class definition by name

    :param ast_tree: AST tree
    :param class_name: name of the class.
    :return: class node found
    """

    class ClassVisitor(ast.NodeVisitor):
        """
        Visitor.
        """

        def __init__(self):
            self.found_class_node = None

        def visit_ClassDef(self, node):  # pylint: disable=invalid-name
            """
            Visit class definition.

            :param node: node.
            :return:
            """
            if node.name == class_name:
                self.found_class_node = node

    visitor = ClassVisitor()
    visitor.visit(ast_tree)

    return visitor.found_class_node


def check_guide_links_in_operator_descriptions() -> None:
    """
    Check if there are links to guides in operator's descriptions.

    """

    def generate_build_error(path, line_no, operator_name):
        return DocBuildError(
            file_path=path,
            line_no=line_no,
            message=(
                f"Link to the guide is missing in operator's description: {operator_name}.\n"
                f"Please add link to the guide to the description in the following form:\n"
                f"\n"
                f".. seealso::\n"
                f"    For more information on how to use this operator, take a look at the guide:\n"
                f"    :ref:`howto/operator:{operator_name}`\n"
            )
        )

    # Extract operators for which there are existing .rst guides
    operator_names = find_existing_guide_operator_names()

    # Extract all potential python modules that can contain operators
    python_module_paths = chain(
        glob(f"{ROOT_PACKAGE_DIR}/operators/*.py"),
        glob(f"{ROOT_PACKAGE_DIR}/sensors/*.py"),
        glob(f"{ROOT_PACKAGE_DIR}/providers/**/operators/*.py", recursive=True),
        glob(f"{ROOT_PACKAGE_DIR}/providers/**/sensors/*.py", recursive=True),
        glob(f"{ROOT_PACKAGE_DIR}/providers/**/transfers/*.py", recursive=True),
    )

    for py_module_path in python_module_paths:
        with open(py_module_path) as f:
            py_content = f.read()

        if "This module is deprecated" in py_content:
            continue

        for existing_operator in operator_names:
            if f"class {existing_operator}" not in py_content:
                continue
            # This is a potential file with necessary class definition.
            # To make sure it's a real Python class definition, we build AST tree
            ast_tree = ast.parse(py_content)
            class_def = extract_ast_class_def_by_name(ast_tree, existing_operator)

            if class_def is None:
                continue

            docstring = ast.get_docstring(class_def)
            if "This class is deprecated." in docstring:
                continue

            if f":ref:`howto/operator:{existing_operator}`" in ast.get_docstring(class_def):
                continue

            build_errors.append(
                generate_build_error(py_module_path, class_def.lineno, existing_operator)
            )


def assert_file_not_contains(file_path: str, pattern: str, message: str) -> None:
    """
    Asserts that file does not contain the pattern. Return message error if it does.

    :param file_path: file
    :param pattern: pattern
    :param message: message to return
    """
    with open(file_path, "rb", 0) as doc_file:
        pattern_compiled = re.compile(pattern)

        for num, line in enumerate(doc_file, 1):
            line_decode = line.decode()
            if re.search(pattern_compiled, line_decode):
                build_errors.append(DocBuildError(file_path=file_path, line_no=num, message=message))


def filter_file_list_by_pattern(file_paths: Iterable[str], pattern: str) -> List[str]:
    """
    Filters file list to thoose tha content matches the pattern

    :param file_paths: file paths to check
    :param pattern: pattern to match
    :return: list of files matching the pattern
    """
    output_paths = []
    pattern_compiled = re.compile(pattern)
    for file_path in file_paths:
        with open(file_path, "rb", 0) as text_file:
            text_file_content = text_file.read().decode()
            if re.findall(pattern_compiled, text_file_content):
                output_paths.append(file_path)
    return output_paths


def find_modules(deprecated_only: bool = False) -> Set[str]:
    """
    Finds all modules.

    :param deprecated_only: whether only deprecated modules should be found.
    :return: set of all modules found
    """
    file_paths = glob(f"{ROOT_PACKAGE_DIR}/**/*.py", recursive=True)
    # Exclude __init__.py
    file_paths = [f for f in file_paths if not f.endswith("__init__.py")]
    if deprecated_only:
        file_paths = filter_file_list_by_pattern(file_paths, r"This module is deprecated.")
    # Make path relative
    file_paths = [os.path.relpath(f, ROOT_PROJECT_DIR) for f in file_paths]
    # Convert filename to module
    modules_names = {file_path.rpartition(".")[0].replace("/", ".") for file_path in file_paths}
    return modules_names


def check_class_links_in_operators_and_hooks_ref() -> None:
    """
    Checks classes and links in the operators and hooks ref.
    """
    with open(os.path.join(DOCS_DIR, "operators-and-hooks-ref.rst")) as ref_file:
        content = ref_file.read()
    current_modules_in_file = set(re.findall(r":mod:`(.+?)`", content))

    airflow_modules = find_modules() - find_modules(deprecated_only=True)
    airflow_modules = {
        o for o in airflow_modules if any(f".{d}." in o for d in
                                          ["operators", "hooks", "sensors", "transfers"])
    }

    missing_modules = airflow_modules - current_modules_in_file
    missing_modules -= {"airflow.providers.google.common.hooks.base_google"}
    if missing_modules:
        module_text_list = " * " + "\n* ".join(missing_modules)
        build_errors.append(
            DocBuildError(
                file_path="operators-and-hooks-ref.rst",
                line_no=0,
                message=(
                    f"New module detected."
                    f"Please add them to the list of operators and hooks - `operators-and-hooks-ref.rst` "
                    f"file.\n"
                    f"\n"
                    f"New modules:\n"
                    f"{module_text_list}"
                ),
            )
        )


def check_guide_links_in_operators_and_hooks_ref() -> None:
    """
    Checks all guide links in operators and hooks references.
    """
    all_guides = glob(f"{DOCS_DIR}/howto/operator/**/*.rst", recursive=True)
    # Remove extension
    all_guides = [
        os.path.relpath(guide, DOCS_DIR).rpartition(".")[0]
        for guide in all_guides
        if "_partials" not in guide
    ]
    # Remove partials and index
    all_guides = [
        guide
        for guide in all_guides
        if "/_partials/" not in guide and not guide.endswith("index")
    ]

    with open(os.path.join(DOCS_DIR, "operators-and-hooks-ref.rst")) as ref_file:
        content = ref_file.read()

    missing_guides = [
        guide
        for guide in all_guides
        if guide not in content
    ]
    if missing_guides:
        guide_text_list = "\n".join(f":doc:`How to use <{guide}>`" for guide in missing_guides)

        build_errors.append(
            DocBuildError(
                file_path="operators-and-hooks-ref.rst",
                line_no=0,
                message=(
                    f"New guide detected. "
                    f"Please add them to the list of operators and hooks - `operators-and-hooks-ref.rst` "
                    f"file.\n"
                    f"You can copy the relevant parts of the link from the section below:\n"
                    f"\n"
                    f"{guide_text_list}"
                ),
            )
        )


def check_exampleinclude_for_example_dags():
    """
    Checks all exampleincludes for  example dags.
    """
    all_docs_files = glob(f"${DOCS_DIR}/**/*rst", recursive=True)

    for doc_file in all_docs_files:
        assert_file_not_contains(
            file_path=doc_file,
            pattern=r"literalinclude::.+example_dags",
            message=(
                "literalinclude directive is prohibited for example DAGs. \n"
                "You should use the exampleinclude directive to include example DAGs."
            )
        )


def check_enforce_code_block():
    """
    Checks all code:: blocks.
    """
    all_docs_files = glob(f"${DOCS_DIR}/**/*rst", recursive=True)

    for doc_file in all_docs_files:
        assert_file_not_contains(
            file_path=doc_file,
            pattern=r"^.. code::",
            message=(
                "We recommend using the code-block directive instead of the code directive. "
                "The code-block directive is more feature-full."
            )
        )


MISSING_GOOGLE_DOC_GUIDES = {
    "ads_to_gcs",
    'adls_to_gcs',
    'bigquery_to_bigquery',
    'bigquery_to_gcs',
    'bigquery_to_mysql',
    'cassandra_to_gcs',
    'dataflow',
    'dlp',
    'gcs_to_bigquery',
    'mssql_to_gcs',
    'postgres_to_gcs',
    'sql_to_gcs',
    'tasks',
}


def check_google_guides():
    """
    Checks Google guides.

    """
    doc_files = glob(f"{DOCS_DIR}/howto/operator/google/**/*.rst", recursive=True)
    doc_names = {f.split("/")[-1].rsplit(".")[0] for f in doc_files}

    operators_files = chain(*[
        glob(f"{ROOT_PACKAGE_DIR}/providers/google/*/{resource_type}/*.py")
        for resource_type in ["operators", "sensors", "transfers"]
    ])
    operators_files = (f for f in operators_files if not f.endswith("__init__.py"))
    operator_names = {f.split("/")[-1].rsplit(".")[0] for f in operators_files}

    # Detect missing docs:
    missing_guide = operator_names - doc_names
    missing_guide -= MISSING_GOOGLE_DOC_GUIDES
    if missing_guide:
        missing_guide_text = " * " + "\n * ".join(missing_guide)
        message = (
            "You've added a new operators, but it looks like you haven't added the guide.\n"
            f"{missing_guide_text}"
            "\n"
            "Could you add it?\n"
        )
        build_errors.append(DocBuildError(file_path=None, line_no=None, message=message))

    # Keep update missing missing guide list
    new_guides = set(doc_names).intersection(set(MISSING_GOOGLE_DOC_GUIDES))
    if new_guides:
        new_guides_text = " * " + "\n * ".join(new_guides)
        message = (
            "You've added a guide currently listed as missing:\n"
            f"{new_guides_text}"
            "\n"
            "Thank you very much.\n"
            "Can you remove it from the list of missing guide, please?"
        )
        build_errors.append(DocBuildError(file_path=__file__, line_no=None, message=message))


def prepare_code_snippet(file_path: str, line_no: int, context_lines_count: int = 5) -> str:
    """
    Prepares code snippet.

    :param file_path: file path
    :param line_no: line number
    :param context_lines_count: number of lines of context.
    :return:
    """

    def guess_lexer_for_filename(filename):
        from pygments.lexers import get_lexer_for_filename
        from pygments.util import ClassNotFound

        try:
            lexer = get_lexer_for_filename(filename)
        except ClassNotFound:
            from pygments.lexers.special import TextLexer
            lexer = TextLexer()
        return lexer

    with open(file_path) as text_file:
        # Highlight code
        code = text_file.read()
        with suppress(ImportError):
            import pygments
            from pygments.formatters.terminal import TerminalFormatter
            code = pygments.highlight(
                code=code, formatter=TerminalFormatter(), lexer=guess_lexer_for_filename(file_path)
            )

        code_lines = code.split("\n")
        # Prepend line number
        code_lines = [f"{line_no:4} | {line}" for line_no, line in enumerate(code_lines, 1)]
        # # Cut out the snippet
        start_line_no = max(0, line_no - context_lines_count)
        end_line_no = line_no + context_lines_count
        code_lines = code_lines[start_line_no:end_line_no]
        # Join lines
        code = "\n".join(code_lines)
    return code


def parse_sphinx_warnings(warning_text: str) -> List[DocBuildError]:
    """
    Parses warnings from Sphinx.

    :param warning_text: warning to parse
    :return: list of DocBuildErrors.
    """
    sphinx_build_errors = []
    for sphinx_warning in warning_text.split("\n"):
        if not sphinx_warning:
            continue
        warning_parts = sphinx_warning.split(":", 2)
        if len(warning_parts) == 3:
            try:
                sphinx_build_errors.append(
                    DocBuildError(
                        file_path=warning_parts[0], line_no=int(warning_parts[1]), message=warning_parts[2]
                    )
                )
            except Exception:  # noqa pylint: disable=broad-except
                # If an exception occurred while parsing the warning message, display the raw warning message.
                sphinx_build_errors.append(
                    DocBuildError(
                        file_path=None, line_no=None, message=sphinx_warning
                    )
                )
        else:
            sphinx_build_errors.append(DocBuildError(file_path=None, line_no=None, message=sphinx_warning))
    return sphinx_build_errors


def parse_spelling_warnings(warning_text: str) -> List[SpellingError]:
    """
    Parses warnings from Sphinx.

    :param warning_text: warning to parse
    :return: list of SpellingError.
    """
    sphinx_spelling_errors = []
    for sphinx_warning in warning_text.split("\n"):
        if not sphinx_warning:
            continue
        warning_parts = None
        match = re.search(r"(.*):(\w*):\s\((\w*)\)\s?(\w*)\s?(.*)", sphinx_warning)
        if match:
            warning_parts = match.groups()
        if warning_parts and len(warning_parts) == 5:
            try:
                sphinx_spelling_errors.append(
                    SpellingError(
                        file_path=warning_parts[0],
                        line_no=int(warning_parts[1]) if warning_parts[1] not in ('None', '') else None,
                        spelling=warning_parts[2],
                        suggestion=warning_parts[3] if warning_parts[3] else None,
                        context_line=warning_parts[4],
                        message=sphinx_warning,
                    )
                )
            except Exception:  # noqa pylint: disable=broad-except
                # If an exception occurred while parsing the warning message, display the raw warning message.
                sphinx_spelling_errors.append(
                    SpellingError(
                        file_path=None,
                        line_no=None,
                        spelling=None,
                        suggestion=None,
                        context_line=None,
                        message=sphinx_warning,
                    )
                )
        else:
            sphinx_spelling_errors.append(
                SpellingError(
                    file_path=None,
                    line_no=None,
                    spelling=None,
                    suggestion=None,
                    context_line=None,
                    message=sphinx_warning,
                )
            )
    return sphinx_spelling_errors


def check_spelling() -> None:
    """
    Checks spelling for sphinx.

    :return:
    """
    extensions_to_use = [
        "sphinxarg.ext",
        "autoapi.extension",
        "sphinxcontrib.spelling",
        "exampleinclude",
        "sphinx.ext.autodoc",
        "sphinx.ext.coverage",
        "sphinx.ext.viewcode",
        "sphinx.ext.graphviz",
        "sphinxcontrib.httpdomain",
        "sphinxcontrib.jinja",
        "docroles",
        "removemarktransform",
    ]

    with NamedTemporaryFile() as tmp_file:
        build_cmd = [
            "sphinx-build",
            "-W",
            "-b",  # builder to use
            "spelling",
            "-d",  # path for the cached environment and doctree files
            "_build/doctrees",
            "-D",  # override the extensions because one of them throws an error on the spelling builder
            f"extensions={','.join(extensions_to_use)}",
            ".",  # path to documentation source files
            "_build/spelling"
        ]
        print("Executing cmd: ", " ".join([shlex.quote(c) for c in build_cmd]))

        completed_proc = run(build_cmd, cwd=DOCS_DIR)  # pylint: disable=subprocess-run-check
        if completed_proc.returncode != 0:
            spelling_errors.append(
                SpellingError(
                    file_path=None, line_no=None, spelling=None, suggestion=None, context_line=None,
                    message=f"Sphinx spellcheck returned non-zero exit status: {completed_proc.returncode}."
                )
            )

            # pylint: disable=subprocess-run-check
            run(f"find {DOCS_DIR} -name '*.spelling' -exec cat {{}} + >> {tmp_file.name}", shell=True)
            tmp_file.seek(0)
            warning_text = tmp_file.read().decode()
            sphinx_build_errors = parse_spelling_warnings(warning_text)
            spelling_errors.extend(sphinx_build_errors)


def build_sphinx_docs() -> None:
    """
    Build documentation for sphinx.
    """
    with NamedTemporaryFile() as tmp_file:
        build_cmd = [
            "sphinx-build",
            "-b",  # builder to use
            "html",
            "-d",  # path for the cached environment and doctree files
            "_build/doctrees",
            "--color",  # do emit colored output
            "-w",  # turn warnings into errors
            tmp_file.name,
            ".",  # path to documentation source files
            "_build/html",  # path to output directory
        ]
        print("Executing cmd: ", " ".join([shlex.quote(c) for c in build_cmd]))

        completed_proc = run(build_cmd, cwd=DOCS_DIR)  # pylint: disable=subprocess-run-check
        if completed_proc.returncode != 0:
            build_errors.append(
                DocBuildError(
                    file_path=None,
                    line_no=None,
                    message=f"Sphinx returned non-zero exit status: {completed_proc.returncode}.",
                )
            )
        tmp_file.seek(0)
        warning_text = tmp_file.read().decode()
        # Remove 7-bit C1 ANSI escape sequences
        warning_text = re.sub(r"\x1B[@-_][0-?]*[ -/]*[@-~]", "", warning_text)
        sphinx_build_errors = parse_sphinx_warnings(warning_text)
        build_errors.extend(sphinx_build_errors)


def print_build_errors_and_exit(message) -> None:
    """
    Prints build errors and exists.

    :param message:
    :return:
    """
    if build_errors or spelling_errors:
        if build_errors:
            display_errors_summary()
            print()
        if spelling_errors:
            display_spelling_error_summary()
            print()
        print(message)
        print()
        print(CHANNEL_INVITATION)
        sys.exit(1)


parser = argparse.ArgumentParser(description='Builds documentation and runs spell checking')
parser.add_argument('--docs-only', dest='docs_only', action='store_true',
                    help='Only build documentation')
parser.add_argument('--spellcheck-only', dest='spellcheck_only', action='store_true',
                    help='Only perform spellchecking')

args = parser.parse_args()

clean_files()

CHANNEL_INVITATION = """\
If you need help, write to #documentation channel on Airflow's Slack.
Channel link: https://apache-airflow.slack.com/archives/CJ1LVREHX
Invitation link: https://s.apache.org/airflow-slack\
"""

print_build_errors_and_exit("The documentation has errors. Fix them to build documentation.")

if not args.docs_only:
    check_spelling()
    print_build_errors_and_exit("The documentation has spelling errors. Fix them to build documentation.")

if not args.spellcheck_only:
    build_sphinx_docs()
    check_guide_links_in_operator_descriptions()
    check_class_links_in_operators_and_hooks_ref()
    check_guide_links_in_operators_and_hooks_ref()
    check_enforce_code_block()
    check_exampleinclude_for_example_dags()
    check_google_guides()
    print_build_errors_and_exit("The documentation has errors.")
