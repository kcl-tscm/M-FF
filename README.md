# Machine learning nonparametric force fields (MFF)

[![DOI](https://zenodo.org/badge/123019663.svg)](https://zenodo.org/badge/latestdoi/123019663)

To read the full documentation check https://mff.readthedocs.io/en/latest/

An example tutorial jupyter notebook can be found in the `tutorials` folder.

![alt text](https://github.com/kcl-tscm/mff/blob/master/docs/_static/mff_logo_2.svg)
## Table of Contents

- [Background on MFFs](#background)
- [Install](#install)
- [Examples](#examples)
- [Maintainers](#maintainers)
- [References](#references)

## Background on MFF

The MFF package uses Gaussian process regression to extract non-parametric 2- and 3- body force fields from ab-initio calculations.
For a detailed description of the theory behind Gaussian process regression to predict forces and/or energies, and an explanation of the mapping technique used, please refer to [1].

For an example use of the MFF package to build 3-body force fields for Ni nanoclusters, please see [2].

## Install

Clone the repo into a folder:

    git clone https://github.com/kcl-tscm/mff.git
    cd mff


If you don't have it, install virtualenv

    pip install virtualenv
 
 
Create a virtual environment using a python 3.6 installation

	virtualenv --python=/usr/bin/python3.6 <path/to/new/virtualenv/>


Activate the new virtual environment 
	
	source <path/to/new/virtualenv/bin/activate>


To install from source run the following command:
    
    python setup.py install
	

Or, to build in place for development, run:
    
    python setup.py develop


## Examples
Refer to the two files in the Tutorial folder for working jupyter notebooks showing most of the functionalities of this package.


## Maintainers

* Claudio Zeni (claudio.zeni@kcl.ac.uk),
* Aldo Glielmo (aldo.glielmo@kcl.ac.uk),
* Ádám Fekete (adam.fekete@kcl.ac.uk).

## References

[1] A. Glielmo, C. Zeni, A. De Vita, *Efficient non-parametric n-body force fields from machine learning* (https://arxiv.org/abs/1801.04823)

[2] C .Zeni, K. Rossi, A. Glielmo, N. Gaston, F. Baletto, A. De Vita *Building machine learning force fields for nanoclusters* (https://arxiv.org/abs/1802.01417)
