import os
import logging

import numpy as np
from ase.io import read

import better_MFF_database
from better_MFF_database import carve_confs

better_MFF_database.USE_ASAP = False
logging.basicConfig(level=logging.INFO)

if __name__ == '__main__':

    r_cut = 3.7
    n_data = 3000

    filename = 'data/C_a/movie.xyz'
    directory = 'data/C_a'

    # Open file and get number of atoms and steps
    traj = read(filename, index=slice(0, 10), format='extxyz')

    elements, confs, forces, energies = carve_confs(traj, r_cut, n_data, forces_label='DFT_force')

    if not os.path.exists(directory):
        os.makedirs(directory)

    np.save('{}/confs_cut={:.2f}.npy'.format(directory, r_cut), confs)
    np.save('{}/forces_cut={:.2f}.npy'.format(directory, r_cut), forces)
    np.save('{}/energies_cut={:.2f}.npy'.format(directory, r_cut), energies)

    lens = [len(conf) for conf in confs]

    logging.info('\n'.join((
        'Number of atoms in a configuration:',
        '   maximum: {}'.format(np.max(lens)),
        '   minimum: {}'.format(np.min(lens)),
        '   average: {:.4}'.format(np.mean(lens))
    )))
