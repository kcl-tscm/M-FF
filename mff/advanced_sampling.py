import logging
import numpy as np
import time
from pathlib import Path
import sys
sys.path.insert(0, '../')
sys.path.insert(0, '../../')
sys.path.insert(0, '../../pymf/pymf/')
from pymf import cur
from mff.models import TwoBodySingleSpeciesModel,  CombinedSingleSpeciesModel
from mff.models import TwoBodyTwoSpeciesModel,  CombinedTwoSpeciesModel
from mff.configurations import carve_from_snapshot
from mff.gp import GaussianProcess 
from mff import kernels
from skbayes.rvm_ard_models import RVR
from sklearn.metrics import mean_squared_error
from scipy.spatial.distance import cdist
import random


logging.basicConfig(level=logging.ERROR)


class Sampling(object):
    """ Sampling methods class
    Class containing sampling methods to optimize the trainng database selection.
    The class is currently set in order to work with local atomic energies, 
    and is therefore made to be used in confined systems (nanoclusters, molecules).
    Some of the mothods used can be applied to force training too (ivm_sampling ), 
    or are independent to the training outputs (grid_2b sampling and grid_3b_sampling).
    These methods can be used on systems with PBCs where a local energy is not well defined.
    The class also initializes two GP objects to use in some of its methods.

    Args:
        confs (list of arrays): List of the configurations as M*5 arrays
        energies (array): Local atomic energies, one per configuration
        forces (array): Forces acting on the central atoms of confs, one per configuration
        sigma_2b (float): Lengthscale parameter of the 2-body kernels in Amstrongs
        sigma_3b (float): Lengthscale parameter of the 3-body kernels in Amstrongs
        sigma_mb (float): Lengthscale parameter of the many-body kernel in Amstrongs
        noise (float): Regularization parameter of the Gaussian process
        r_cut (float): Cutoff function for the Gaussian process
        theta (float): Decay lengthscale of the cutoff function for the Gaussian process
            
    Attributes:
        elements (list): List of the atomic number of the atoms present in the system
        natoms (int): Number of atoms in the system, used for nanoclusters
        K2 (array): Gram matrix for the energy-energy 2-body kernel using the full reduced dataset
        K3 (array): Gram matrix for the energy-energy 3-body kernel using the full reduced dataset

    """

    def __init__(self, confs=None, energies=None,
                 forces=None, sigma_2b = 0.05, sigma_3b = 0.1, sigma_mb = 0.2, noise = 0.001, r_cut = 8.5, theta = 0.5):
        
        self.confs = confs
        self.energies = energies
        self.forces = forces
        natoms = len(confs[0]) + 1
        atom_number_list = [conf[0,3] for conf in confs]
        self.elements = np.unique(atom_number_list, return_counts=False)
        self.natoms = natoms
        self.K2 = None
        self.K3 = None
        self.sigma_2b, self.sigma_3b, self.sigma_mb, self.noise, self.r_cut, self.theta = (
            sigma_2b, sigma_3b, sigma_mb, noise, r_cut, theta)
        self.get_the_right_kernel('2b')
        self.get_the_right_kernel('3b')
        

    def read_xyz(self, filename, r_cut, randomized = True, shuffling = True, forces_label=None, energy_label=None):
        from ase.io import read
        traj = read(filename, index=slice(None), format='extxyz')
        confs, forces, energies = [], [], []
        for i in np.arange(len(traj)):
            if randomized:
                rand = np.random.randint(0, len(traj[i]), 1)
            else:
                rand = 0                
            co, fe, en = carve_from_snapshot(traj[i], rand, r_cut, forces_label=forces_label, energy_label=energy_label)
            if len(co[0]) == self.natoms - 1:
                confs.append(co[0])
                forces.append(fe)
                energies.append(en)
        
        confs = np.reshape(confs, (len(confs), self.natoms-1, 5))
        forces = np.reshape(forces, (len(forces), 3))

        # Bring energies to zero mean
        energies = np.reshape(energies, len(energies))
        energies -= np.mean(energies)
        
        if shuffling:
            shuffled_order = np.arange(len(energies))
            random.shuffle(shuffled_order)
            energies, forces, confs = energies[shuffled_order], forces[shuffled_order], confs[shuffled_order]

        self.reduced_energies = energies
        self.reduced_forces = forces
        self.reduced_confs = confs
        del confs, energies, forces, shuffled_order, traj
                                         
    def clean_dataset(self, randomized = True, shuffling = True):
        ''' 
        Function used to subsample from a complete trajectory only one atomic environment
        per snapshot. This is necessary when training on energies of nanoclusters in order to assign
        an unique energy value to every configuration and to avoid using redundant information 
        in the form of local atomic environments centered around different atoms in the same snapshot.
        
        Args:
            randomized (bool): If True, an atom at random is chosen every snapshot, if false always the first
                atom in the configurations will be chosen to represent said snapshot.
            shuffling (bool): if True, once the dataset is created, it is shuffled randomly in order to 
                avoid any bias during incremental training set optimization methods (e.g. rvm, cur, ivm).
                
        '''
        confs, energies, forces = self.confs, self.energies, self.forces
        natoms = self.natoms
        
        # Transform confs into a numpy array
        arrayed_confs = np.zeros((len(forces), natoms-1, 5))
        for i in np.arange(len(confs)):
            try:
                arrayed_confs[i] = confs[i][:natoms-1, :]
            except:
                print("Number of atoms in the configurations is not the expected one")
                np.delete(arrayed_confs, i)
                np.delete(confs, i)
                np.delete(forces, i)
            
        # Bring energies to zero mean
        energies = np.reshape(energies, len(energies))
        energies -= np.mean(energies)

        # Extract one conf, energy and force per snapshot
        # The particular atom can be chosen at random (random = True)
        # or be always the same (random = False).
        reduced_energies = np.zeros(len(confs)//natoms)
        reduced_confs    = np.zeros((len(confs)//natoms, natoms-1, 5))
        reduced_forces   = np.zeros((len(confs)//natoms, 3))
        for i in np.arange(len(confs)//natoms):
            if randomized:
                rand = np.random.randint(0, natoms, 1)
            else:
                rand = 0
            reduced_confs[i]    =    arrayed_confs[i*natoms+rand]
            reduced_energies[i] = energies[i*natoms+rand]
            reduced_forces[i]   =   forces[i*natoms+rand]

        if shuffling:
            shuffled_order = np.arange(len(reduced_energies))
            random.shuffle(shuffled_order)
            reduced_energies, reduced_forces, reduced_confs = (
                reduced_energies[shuffled_order], reduced_forces[shuffled_order], reduced_confs[shuffled_order])
        
        self.reduced_energies = reduced_energies
        self.reduced_forces = reduced_forces
        self.reduced_confs = reduced_confs
        del confs, energies, forces, natoms, reduced_confs, reduced_forces, reduced_energies, shuffled_order
        
        
    def get_the_right_model(self, ker):
        if len(self.elements) == 1:
            if ker == '2b':
                return TwoBodySingleSpeciesModel(self.elements, self.r_cut, self.sigma_2b, self.theta, self.noise)
            elif ker == '3b':
                return CombinedSingleSpeciesModel(element=self.elements, noise=self.noise, sigma_2b=self.sigma_2b
                                                   , sigma_3b=self.sigma_3b, theta_3b=self.theta, r_cut=self.r_cut, theta_2b=self.theta)
            else:
                print('Kernel type not understood, shutting down')
                return 0 
            
        else:
            if ker == '2b':
                return TwoBodyTwoSpeciesModel(self.elements, self.r_cut, self.sigma_2b, self.theta, self.noise)
            elif ker == '3b':
                return CombinedTwoSpeciesModel(elements=self.elements, noise=self.noise, sigma_2b=self.sigma_2b
                                                   , sigma_3b=self.sigma_3b, theta_3b=self.theta, r_cut=self.r_cut, theta_2b=self.theta)
            else:
                print('Kernel type not understood, shutting down')
                return 0      
        
    def get_the_right_kernel(self, ker):
        if len(self.elements) == 1:
            if ker == '2b':
                self.gp2 = GaussianProcess(kernel= kernels.TwoBodySingleSpeciesKernel(
                    theta=[self.sigma_2b, self.theta, self.r_cut]), noise= self.noise)
                self.gp2.nnodes = 1
            elif ker == '3b':
                self.gp3 = GaussianProcess(kernel= kernels.ThreeBodySingleSpeciesKernel(
                    theta=[self.sigma_3b, self.theta, self.r_cut]), noise= self.noise)
                self.gp3.nnodes = 1
            else:
                print('Kernel type not understood, shutting down')
                return 0 
            
        else:
            if ker == '2b':
                self.gp2 = GaussianProcess(kernel= kernels.TwoBodyTwoSpeciesKernel(
                    theta=[self.sigma_2b, self.theta, self.r_cut]), noise= self.noise)
                self.gp2.nnodes = 1
            elif ker == '3b':
                self.gp3 = GaussianProcess(kernel= kernels.ThreeBodyTwoSpeciesKernel(
                    theta=[self.sigma_3b, self.theta, self.r_cut]), noise= self.noise)
                self.gp3.nnodes = 1
            else:
                print('Kernel type not understood, shutting down')
                return 0     
        
        
    def train_test_split(self, confs=[], forces=[], energies=[], ntest = 10):
        ''' 
        Function used to subsample a training and a test set: the test set is extracted at random
        and the remaining dataset is trated as a training set (from which we then subsample using the various methods).
        
        Args:
            confs (array or list): List of the configurations as M*5 arrays
            energies (array): Local atomic energies, one per configuration
            forces (array): Forces acting on the central atoms of confs, one per configuration
            ntest (int): Number of test points, if None, every point that is not a training point will be used
                as a test point
        
        '''
        ind = np.arange(len(confs))
        ind_test = np.random.choice(ind, size=ntest, replace=False)
        ind_train = list(set(ind) - set(ind_test))
        self.X, self.Y, self.Y_force = confs[ind_train], energies[ind_train], forces[ind_train]
        self.x, self.y, self.y_force = confs[ind_test], energies[ind_test], forces[ind_test]
        del ind, ind_test, ind_train, confs, energies, forces
        try:
            del self.reduced_energies, self.reduced_confs, self.reduced_forces
        except:
            pass
        

    def ker_2b(self, X1, X2):
        X1, X2 = np.reshape(X1, (18,5)), np.reshape(X2, (18,5))
        ker = self.gp2.kernel.k2_ee(X1, X2, sig=self.sigma_2b, rc=self.r_cut, theta=self.theta)
        del X1, X2
        return ker

    
    def ker_3b(self, X1, X2):
        X1, X2 = np.reshape(X1, (18,5)), np.reshape(X2, (18,5))
        ker = self.gp3.kernel.k3_ee(X1, X2, sig=self.sigma_3b, rc=self.r_cut, theta=self.theta)
        del X1, X2
        return ker

    
    def normalized_3b(self, X1, X2):
        X1, X2 = np.reshape(X1, (18,5)), np.reshape(X2, (18,5))
        ker = self.gp3.kernel.k3_ee(X1, X2, sig=self.sigma_3b, rc=self.r_cut, theta=self.theta)
        ker_11 = self.gp3.kernel.k3_ee(X1, X1, sig=self.sigma_3b, rc=self.r_cut, theta=self.theta)
        ker_22 = self.gp3.kernel.k3_ee(X2, X2, sig=self.sigma_3b, rc=self.r_cut, theta=self.theta)
        ker2 = np.square(ker/np.sqrt(ker_11*ker_22))
        del ker_11, ker_22, X1, X2, ker
        return ker2

    
    def ker_mb(self, X1, X2):
        X1, X2 = np.reshape(X1, (18,5)), np.reshape(X2, (18,5))
        X1, X2 = X1[:,:3], X2[:,:3]
        outer = X1[:,None,:] - X2[None, :,:]
        ker = np.exp(-(np.sum(np.square(outer)/(2.0*self.sigma_mb**2), axis = 2)))
        ker = np.einsum('ij -> ', ker)
        del outer, X1, X2
        return ker       
      
        
    def rvm(self, method = '2b',  batchsize = 1000):
        t0 = time.time()
        if method == '2b':
            rvm = RVR(kernel = self.ker_2b)
        if method == '3b':
            rvm = RVR(kernel = self.ker_3b)
        if method == 'mb':
            rvm = RVR(kernel = self.ker_mb)
        if method == 'normalized_3b':
            rvm = RVR(kernel = self.normalized_3b)
            
        split = len(self.X)//batchsize + 1    # Decide the number of batches
        batches = np.array_split(range(len(self.X)),split)  # Create a number of evenly sized batches
        reshaped_X, reshaped_x = np.reshape(self.X, (len(self.X), 5*(self.natoms-1))), np.reshape(self.x, (len(self.x), 5*(self.natoms-1)))
        index = []
        for s in np.arange(len(batches)):
            batch_index = list(set(index).union(batches[s]))
            rvm.fit(reshaped_X[batch_index], self.Y[batch_index])
            index = np.asarray(batch_index)[rvm.active_]
        y_hat,var     = rvm.predict_dist(reshaped_x)
        error = y_hat - self.y
        MAE = np.mean(np.abs(error))
        SMAE = np.std(np.abs(error))
        RMSE = np.sqrt(np.mean((error) ** 2))  
        del var, rvm, split, batches, batch_index, reshaped_X, reshaped_x, y_hat, error
        tf = time.time()
        return MAE, SMAE, RMSE, list(index), tf-t0

    
    def ivm_e(self, method = '2b', ntrain = 500, batchsize = 1000,  use_pred_error = True):
        t0 = time.time()
        m = self.get_the_right_model(method)
        ndata = len(self.Y)
        mask = np.ones(ndata).astype(bool)
        randints = random.sample(range(ndata), 2)
        m.fit_energy(self.X[randints], self.Y[randints])
        mask[randints] = False
        for i in np.arange(min(ntrain-2, ndata-2)):
            if batchsize > ndata-i-2:
                batchsize = ndata-i-2
            rand_test = random.sample(range(ndata-2-i), batchsize)
            if use_pred_error:
                pred, pred_var =  m.predict_energy(self.X[mask][rand_test], return_std = True)
                worst_thing = np.argmax(pred_var)  # L1 norm
            else:
                pred = m.predict_energy(self.X[mask][rand_test])
                worst_thing = np.argmax(abs(pred - self.Y[mask][rand_test]))  # L1 norm
            m.update_energy(self.X[mask][worst_thing], self.Y[mask][worst_thing])
            mask[rand_test[worst_thing]] = False

        y_hat = m.predict_energy(self.x)
        error = y_hat - self.y
        MAE = np.mean(np.abs(error))
        SMAE = np.std(np.abs(error))
        RMSE = np.sqrt(np.mean((error) ** 2)) 
        index = np.arange(len(self.X))[~mask]
        del mask, worst_thing, pred, rand_test, m, ndata, randints, y_hat
        tf = time.time()
        return MAE, SMAE, RMSE, list(index), tf-t0

    
    def ivm_f(self, method = '2b',  ntrain = 500, batchsize = 1000, use_pred_error = True, error_metric = 'energy'):
        t0 = time.time()
        m = self.get_the_right_model(method)
        ndata = len(self.Y_force)
        mask = np.ones(ndata).astype(bool)
        randints = random.sample(range(ndata), 2)
        m.fit(self.X[randints], self.Y_force[randints])
        mask[randints] = False
        for i in np.arange(min(ntrain-2, ndata-2)):
            if batchsize > ndata-i-2:
                batchsize = ndata-i-2
            rand_test = random.sample(range(ndata-2-i), batchsize)
            if use_pred_error:
                pred, pred_var =  m.predict(self.X[mask][rand_test], return_std = True)
                worst_thing = np.argmax(np.sum(np.abs(pred_var), axis = 1))  
                # L1 norm
            else:
                pred = m.predict(self.X[mask][rand_test])
                worst_thing = np.argmax(np.sum(abs(pred - self.Y_force[mask][rand_test]), axis =1))  # L1 norm
            m.update_force(self.X[mask][worst_thing], self.Y_force[mask][worst_thing])
            mask[rand_test[worst_thing]] = False
        
        if error_metric == 'force':
            y_hat = m.predict(self.x)
            error = y_hat - self.y_force
            MAE = np.mean(np.sqrt(np.sum(np.square(error), axis=1)))
            SMAE = np.std(np.sqrt(np.sum(np.square(error), axis=1)))
            RMSE = np.sqrt(np.mean((error) ** 2))   
        else:
            y_hat = m.predict_energy(self.x)
            error = y_hat - self.y
            MAE = np.mean(np.abs(error))
            SMAE = np.std(np.abs(error))
            RMSE = np.sqrt(np.mean((error) ** 2))  
        index_return = np.arange(len(self.X))[~mask]
        del mask, worst_thing, pred, rand_test, m, ndata, randints, error
        tf = time.time()
        return MAE, SMAE, RMSE, list(index_return), tf-t0
    
    
    def grid(self, method = '2b', nbins = 100, error_metric = 'energy'):
        t0 = time.time()
        if method == '2b':
            stored_histogram = np.zeros(nbins)
            index = []
            ind = np.arange(len(self.X))
            randomarange = np.random.choice(ind, size=len(self.X), replace=False)
            for j in randomarange: # for every snapshot of the trajectory file
                distances = np.sqrt(np.einsum('id -> i', np.square(self.X[j][:,:3])))
                distances[np.where(distances > self.r_cut)] = None
                this_snapshot_histogram = np.histogram(distances, nbins, (0.0, self.r_cut))
                if (stored_histogram - this_snapshot_histogram[0] < 0).any():
                    index.append(j)
                    stored_histogram += this_snapshot_histogram[0]

            m = TwoBodySingleSpeciesModel(self.elements, self.r_cut, self.sigma_2b, self.theta, self.noise)
            
        elif method == '3b':
            stored_histogram = np.zeros((nbins, nbins, nbins))
            index = []
            ind = np.arange(len(self.X))
            randomarange = np.random.choice(ind, size=len(self.X), replace=False)
            for j in randomarange: # for every snapshot of the trajectory file
                atoms = np.vstack(([0., 0., 0.], self.X[j][:,:3]))
                distances  = cdist(atoms, atoms)
                distances[np.where(distances > self.r_cut)] = None
                distances[np.where(distances == 0 )] = None
                triplets = []
                for k in np.argwhere(distances[:,0] > 0 ):
                    for l in np.argwhere(distances[0,:] > 0 ):
                        if distances[k,l] > 0 :
                            triplets.append([distances[0, k], distances[0, l], distances[k, l]])
                            triplets.append([distances[0, l], distances[k, l], distances[0, k]])
                            triplets.append([distances[k, l], distances[0, k], distances[0, l]])

                triplets = np.reshape(triplets, (len(triplets), 3)) 
                this_snapshot_histogram = np.histogramdd(triplets, bins = (nbins, nbins, nbins), 
                                                         range =  ((0.0, self.r_cut), (0.0, self.r_cut), (0.0, self.r_cut)))

                if (stored_histogram - this_snapshot_histogram[0] < 0).any():
                    index.append(j)
                    stored_histogram += this_snapshot_histogram[0]

            m = CombinedTwoSpeciesModel(elements=self.elements, noise=self.noise, sigma_2b=self.sigma_2b
                                                   , sigma_3b=self.sigma_3b, theta_3b=self.theta, r_cut=self.r_cut, theta_2b=self.theta)
        else:
            print('Method must be either 2b or 3b')
            return 0

        if error_metric == 'force':
            m.fit(self.X[index], self.Y_force[index])
            y_hat = m.predict(self.x)
            error = y_hat - self.y_force
            MAE = np.mean(np.sqrt(np.sum(np.square(error), axis=1)))
            SMAE = np.std(np.sqrt(np.sum(np.square(error), axis=1)))
            RMSE = np.sqrt(np.mean((error) ** 2))    
        else:
            m.fit_energy(self.X[index], self.Y[index])
            y_hat = m.predict_energy(self.x)
            error = y_hat - self.y
            MAE = np.mean(np.abs(error))
            SMAE = np.std(np.abs(error))
            RMSE = np.sqrt(np.mean((error) ** 2))   
        del m, distances, this_snapshot_histogram, randomarange, stored_histogram, y_hat, error
        tf = time.time()
        return MAE, SMAE, RMSE, list(index), tf-t0

    
    def cur(self, method = '2b', ntrain = 1000, batchsize = 500):
        t0 = time.time()
        ntrain = ntrain//2 + 1    # Needed since we take the columns AND rows from the decomposition
        split = len(self.X)//batchsize + 1    # Decide the number of batches
        batches = np.array_split(range(len(self.X)),split)  # Create a number of evenly sized batches
        index = []
        for s in np.arange(split):
            batch_index = list(set(index).union(batches[s]))  # For the last batch, take the last batchsize points
            complete_batch = self.X[batch_index]
            if method == '2b':
                gram = self.gp2.calc_gram_ee(complete_batch)
            elif method == '3b':
                gram = self.gp3.calc_gram_ee(complete_batch)   
            else:
                print('Method must be either 2b or 3b')
                return 0
            c = cur.CUR(gram,rrank=ntrain, crank=ntrain)
            c.factorize()
            index = []
            for i in np.arange(len(batch_index)):    # For all points in the batch
                for j in np.arange(ntrain):        # For all points in the decomposition
                    if(all(gram[:,i] == c.U[:,j]) or all(gram[i,:] == c.V[j,:])):
                        index.append(batch_index[i])
            index = list(set(index))
        
        m = self.get_the_right_model(method)
        m.fit_energy(self.X[index], self.Y[index])
        y_hat = m.predict_energy(self.x)
        error = y_hat - self.y
        MAE = np.mean(np.abs(error))
        SMAE = np.std(np.abs(error))
        RMSE = np.sqrt(np.mean((error) ** 2)) 
        index_return = np.arange(len(self.X))[index]
        del m, index, c, gram, ntrain, complete_batch, batch_index, error, y_hat
        tf = time.time()
        return MAE, SMAE, RMSE, list(index_return), tf-t0
                    
        
    def random(self, method = '2b', ntrain = 500, error_metric = 'energy'):
        t0 = time.time()
        ind = np.arange(len(self.X))
        ind_train = np.random.choice(ind, size=ntrain, replace=False)
        train_confs = self.X[ind_train]
        train_energy = self.Y[ind_train]
        train_forces = self.Y_force[ind_train]
        m = self.get_the_right_model(method)
        if error_metric == 'force':
            m.fit(train_confs, train_forces)
            y_hat = m.predict(self.x)
            error = y_hat - self.y_force
            MAE = np.mean(np.sqrt(np.sum(np.square(error), axis=1)))
            SMAE = np.std(np.sqrt(np.sum(np.square(error), axis=1)))
            RMSE = np.sqrt(np.mean((error) ** 2))    
        else:
            m.fit_energy(train_confs, train_forces)
            y_hat = m.predict_energy(self.x)
            error = y_hat - self.y
            MAE = np.mean(np.abs(error))
            SMAE = np.std(np.abs(error))
            RMSE = np.sqrt(np.mean((error) ** 2))   
            
        del m, train_confs, train_energy, train_forces, error, y_hat
        tf = time.time()
        return MAE, SMAE, RMSE, list(ind_train), tf-t0
    

    def test_forces(self, index, method = '2b', sig_2b = 0.2, sig_3b = 0.8, noise = 0.001):
        self.sigma_2b, self.sigma_3b, self.noise = sig_2b, sig_3b, noise
        m = self.get_the_right_model(method)
        m.fit(self.X[index], self.Y_force[index])
        y_hat = m.predict(self.x)
        error = self.y_force - y_hat
        MAEF = np.mean(np.sqrt(np.sum(np.square(error), axis=1)))
        SMAEF = np.std(np.sqrt(np.sum(np.square(error), axis=1)))
        RMSE = np.sqrt(np.mean((error) ** 2))   
        print("MAEF: %.4f SMAEF: %.4f RMSE: %.4f" %(MAEF, SMAEF, RMSE))
        del m, error, y_hat, index
        return MAEF, SMAEF, RMSE
    