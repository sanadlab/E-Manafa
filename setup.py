import setuptools
from setuptools import setup
#from manafa._version import __version__

with open("README.md", "r", encoding='utf-8') as fh:
    long_description = fh.read()

setup(
    name='manafa',
    description='E-MANAFA: Energy Monitor and ANAlyzer For Android',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='Rui Rua',
    author_email='rui.rrua@gmail.com',
    url='https://github.com/RRua/e-manafa',
    license='MIT',
    packages=setuptools.find_packages(exclude=['manafa.tests', 'manafa.tests.*']),
    use_incremental=True,
    install_requires=[
    'pytz>=2024.1',  # no upper limit: a new release has the current timezone rules
    'termcolor>=1.1.0',
    'python-textops3>=3.2.1',
    'more-itertools>=8.0.0',
    'perfetto>=0.58.2',
],
    extras_require={'sunburst': ['plotly>=5.0.0'], 'mcp': ['mcp>=1.2.0']},
    setup_requires=['incremental'],
    include_package_data=True,
    scripts=['bin/emanafa'],
    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Intended Audience :: Science/Research',
        'Operating System :: POSIX',
        'Programming Language :: Python :: 3.8',
    ],
    project_urls={
        'Bug Tracker':  'https://github.com/RRua/e-manafa/issues'
        },
    python_requires=">=3.8",
)