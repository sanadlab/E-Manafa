# Configuration file for the Sphinx documentation builder.
# Build the pages with: cd docs_src && make html && cp -r build/html/. ../docs/
import os
import re
import sys
sys.path.insert(0, os.path.abspath('../..'))


project = 'E-MANAFA'
copyright = '2021-2026, Rui Rua'
author = 'Rui Rua'
with open(os.path.abspath('../../manafa/_version.py')) as f:
    release = ".".join(re.search(r'Version\("manafa", (\d+), (\d+), (\d+)\)', f.read()).groups())

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',
]
# the tool needs a device for these imports to do work, not for the import itself.
# packages that are optional are not needed to build the pages
autodoc_mock_imports = ['plotly', 'perfetto']
autodoc_member_order = 'bysource'

exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
html_title = 'E-MANAFA %s' % release
html_theme_options = {'navigation_depth': 3, 'collapse_navigation': False}
html_css_files = ['custom.css']
html_baseurl = 'https://sanadlab.github.io/E-MANAFA/'
