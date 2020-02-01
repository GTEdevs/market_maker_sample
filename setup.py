#!/usr/bin/env python
from setuptools import setup
from os.path import dirname, join

import market_maker


here = dirname(__file__)


setup(name='gte-market-maker',
      version=market_maker.__version__,
      description='Market making bot for GTE API',
      url='https://github.com/gteapp/market_maker_sample',
      long_description=open(join(here, 'README.md')).read(),
      long_description_content_type='text/markdown',
      author='',
      author_email='fake_email@gte.com',
      install_requires=[
          'requests',
          'websocket-client',
          'future'
      ],
      packages=['market_maker', 'market_maker.auth', 'market_maker.utils', 'market_maker.ws'],
      entry_points={
          'console_scripts': ['marketmaker = market_maker:run']
      }
      )
