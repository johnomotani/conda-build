# (c) Continuum Analytics, Inc. / http://continuum.io
# All Rights Reserved
#
# conda is distributed under the terms of the BSD 3-clause license.
# Consult LICENSE.txt or http://opensource.org/licenses/BSD-3-Clause.

from __future__ import absolute_import, division, print_function

import argparse
from os.path import isdir
import shutil
import sys
import warnings

import conda.config as cc
from conda.cli.common import add_parser_channels
from conda.install import delete_trash

import conda_build.api as api
import conda_build.build as build
from conda_build.main_render import (set_language_env_vars, RecipeCompleter,
                                     render_recipe, get_render_parser, bldpkg_path)
import conda_build.source as source
from conda_build.utils import find_recipe, get_recipe_abspath

on_win = (sys.platform == 'win32')


def main():
    p = get_render_parser()
    p.description = """
Tool for building conda packages. A conda package is a binary tarball
containing system-level libraries, Python modules, executable programs, or
other components. conda keeps track of dependencies between packages and
platform specifics, making it simple to create working environments from
different sets of packages."""
    p.add_argument(
        "--check",
        action="store_true",
        help="Only check (validate) the recipe.",
    )
    p.add_argument(
        "--no-anaconda-upload",
        action="store_false",
        help="Do not ask to upload the package to anaconda.org.",
        dest='anaconda_upload',
        default=cc.binstar_upload,
    )
    p.add_argument(
        "--no-binstar-upload",
        action="store_false",
        help=argparse.SUPPRESS,
        dest='anaconda_upload',
        default=cc.binstar_upload,
    )
    p.add_argument(
        "--no-include-recipe",
        action="store_false",
        help="Don't include the recipe inside the built package.",
        dest='include_recipe',
        default=True,
    )
    p.add_argument(
        '-s', "--source",
        action="store_true",
        help="Only obtain the source (but don't build).",
    )
    p.add_argument(
        '-t', "--test",
        action="store_true",
        help="Test package (assumes package is already built).  RECIPE_DIR argument can be either "
        "recipe directory, in which case source download may be necessary to resolve package"
        "version, or path to built package .tar.bz2 file, in which case no source is necessary.",
    )
    p.add_argument(
        '--no-test',
        action='store_true',
        dest='notest',
        help="Do not test the package.",
    )
    p.add_argument(
        '-b', '--build-only',
        action="store_true",
        help="""Only run the build, without any post processing or
        testing. Implies --no-test and --no-anaconda-upload.""",
    )
    p.add_argument(
        '-p', '--post',
        action="store_true",
        help="Run the post-build logic. Implies --no-test and --no-anaconda-upload.",
    )
    p.add_argument(
        'recipe',
        action="store",
        metavar='RECIPE_PATH',
        nargs='+',
        choices=RecipeCompleter(),
        help="Path to recipe directory.",
    )
    p.add_argument(
        '--skip-existing',
        action='store_true',
        help="""Skip recipes for which there already exists an existing build
        (locally or in the channels). """
    )
    p.add_argument(
        '--keep-old-work',
        action='store_true',
        help="""Keep any existing, old work directory. Useful if debugging across
        callstacks involving multiple packages/recipes. """
    )
    p.add_argument(
        '--dirty',
        action='store_true',
        help='Do not remove work directory or _build environment, '
        'to speed up debugging.  Does not apply patches or download source.'
    )
    p.add_argument(
        '-q', "--quiet",
        action="store_true",
        help="do not display progress bar",
    )
    p.add_argument(
        '--token',
        help="Token to pass through to anaconda upload"
    )
    p.add_argument(
        '--user',
        help="User/organization to upload packages to on anaconda.org"
    )
    p.add_argument(
        "--no-activate",
        action="store_false",
        help="do not activate the build and test envs; just prepend to PATH",
        dest='activate',
    )

    add_parser_channels(p)
    p.set_defaults(func=execute)

    args = p.parse_args()
    args_func(args, p)


def output_action(metadata):
    print(bldpkg_path(metadata))


def source_action(metadata):
    source.provide(metadata.path, metadata.get_section('source'))
    print('Source tree in:', source.get_dir())


def test_action(metadata):
    return api.test(metadata.path, move_broken=False)


def check_action(metadata):
    return api.check(metadata.path)


def execute(args, parser):
    build.check_external()

    # change globals in build module, see comment there as well
    build.channel_urls = args.channel or ()
    build.override_channels = args.override_channels
    build.verbose = not args.quiet

    if on_win:
        try:
            # needs to happen before any c extensions are imported that might be
            # hard-linked by files in the trash. one of those is markupsafe,
            # used by jinja2. see https://github.com/conda/conda-build/pull/520
            delete_trash(None)
        except:
            # when we can't delete the trash, don't crash on AssertionError,
            # instead inform the user and try again next time.
            # see https://github.com/conda/conda-build/pull/744
            warnings.warn("Cannot delete trash; some c extension has been "
                          "imported that is hard-linked by files in the trash. "
                          "Will try again on next run.")

    set_language_env_vars(args, parser, execute=execute)

    action = None
    if args.output:
        action = output_action
    elif args.test:
        action = test_action
    elif args.source:
        action = source_action
    elif args.check:
        action = check_action

    if action:
        for recipe in args.recipe:
            recipe_dir, need_cleanup = get_recipe_abspath(recipe)

            # recurse looking for meta.yaml that is potentially not in immediate folder
            recipe_dir = find_recipe(recipe_dir)
            if not isdir(recipe_dir):
                sys.exit("Error: no such directory: %s" % recipe_dir)

            # this fully renders any jinja templating, throwing an error if any data is missing
            m, need_source_download = render_recipe(recipe_dir, no_download_source=False,
                                                    verbose=False, dirty=args.dirty)
            action(m)

            if need_cleanup:
                shutil.rmtree(recipe_dir)
    else:
        api.build(args.recipe, build_only=args.build_only, post=args.post,
                   notest=args.notest, anaconda_upload=args.anaconda_upload,
                   skip_existing=args.skip_existing, keep_old_work=args.keep_old_work,
                   include_recipe=args.include_recipe, already_built=None,
                   token=args.token, user=args.user, dirty=args.dirty)


def args_func(args, p):
    try:
        args.func(args, p)
    except RuntimeError as e:
        if 'maximum recursion depth exceeded' in str(e):
            print_issue_message(e)
            raise
        sys.exit("Error: %s" % e)
    except Exception as e:
        print_issue_message(e)
        raise  # as if we did not catch it


def print_issue_message(e):
    if e.__class__.__name__ not in ('ScannerError', 'ParserError'):
        message = """\
An unexpected error has occurred, please consider sending the
following traceback to the conda GitHub issue tracker at:

    https://github.com/conda/conda-build/issues

Include the output of the command 'conda info' in your report.

"""
        print(message, file=sys.stderr)

if __name__ == '__main__':
    main()
