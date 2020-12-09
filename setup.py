from setuptools import setup, find_packages
from pathlib import Path

here = Path(__file__).parent.resolve()
long_description = (here / 'README.md').read_text(encoding='utf-8')

setup(
    name='pysandboxie',
    version='0.1.0',
    description='Sandboxie binding for Python',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/nedsociety/pysandboxie',
    author='Ned Son',
    author_email='nedsociety@gmail.com',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'License :: OSI Approved :: MIT License',
        'Operating System :: Microsoft :: Windows',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3.9',
    ],
    keywords=['sandboxie', 'sandboxie-plus'],

    packages=find_packages(),
    test_suite='sandboxie.tests',

    python_requires='>=3.9',
    install_requires=['pywin32', 'clize'],
    extras_require={
        'dev': ['pytest', 'coverage', 'pytest-cov', 'debugpy']
    }
)
