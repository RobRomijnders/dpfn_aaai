from setuptools import setup, find_packages

setup(name='dpfn',
      version='0.1',
      description='',
      url='github.com/robromijnders/dpfn_python',
      author='Rob Romijnders',
      author_email='romijndersrob@gmail.com',
      license='LICENSE.txt',
      install_requires=[
          'numpy',
          'scipy',
          'sklearn',
          'matplotlib',
      ],
      packages=find_packages(exclude=('tests')),
      zip_safe=False)
