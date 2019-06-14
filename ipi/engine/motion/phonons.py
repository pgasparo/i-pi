"""Contains the classes that deal with the different dynamics required in
different types of ensembles.

Copyright (C) 2013, Joshua More and Michele Ceriotti

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http.//www.gnu.org/licenses/>.
"""

__all__ = ['DynMatrixMover']

import numpy as np
import time


from ipi.engine.motion import Motion
from ipi.utils.depend import *
from ipi.utils import units
from ipi.utils.phonontools import apply_asr
from ipi.utils.softexit import softexit
from ipi.utils.messages import verbosity, warning, info


class DynMatrixMover(Motion):

    """Dynamic matrix calculation routine by finite difference.
    """

    def __init__(self, fixcom=False, fixatoms=None, mode="fd", energy_shift=0.0, pos_shift=0.001, output_shift=0.000, dynmat=np.zeros(0, float), refdynmat=np.zeros(0, float), prefix="", asr="none"):
        """Initialises DynMatrixMover.
        Args:
        fixcom  : An optional boolean which decides whether the centre of mass
                  motion will be constrained or not. Defaults to False.
        dynmatrix : A 3Nx3N array that stores the dynamic matrix.
        refdynmatrix : A 3Nx3N array that stores the refined dynamic matrix.
        """

        super(DynMatrixMover, self).__init__(fixcom=fixcom, fixatoms=fixatoms)

        # Finite difference option.
        self.mode = mode
        if self.mode == "fd":
            self.phcalc = FDPhononCalculator()
        elif self.mode == "nmfd":
            self.phcalc = NMFDPhononCalculator()
        elif self.mode == "enmfd":
            self.phcalc = ENMFDPhononCalculator()

        self.deltaw = output_shift
        self.deltax = pos_shift
        self.deltae = energy_shift
        self.dynmatrix = dynmat
        self.refdynmatrix = refdynmat
        self.frefine = False
        self.U = None
        self.V = None
        self.prefix = prefix
        self.asr = asr

        if self.prefix == "":
            self.prefix = "phonons"

    def bind(self, ens, beads, nm, cell, bforce, prng, omaker):

        super(DynMatrixMover, self).bind(ens, beads, nm, cell, bforce, prng, omaker)

        # Raises error for nbeads not equal to 1.
        if(self.beads.nbeads > 1):
            raise ValueError("Calculation not possible for number of beads greater than one.")

        self.ism = 1 / np.sqrt(dstrip(self.beads.m3[-1]))
        self.m = dstrip(self.beads.m)
        self.phcalc.bind(self)

        self.dbeads = self.beads.copy()
        self.dcell = self.cell.copy()
        self.dforces = self.forces.copy(self.dbeads, self.dcell)

    def step(self, step=None):
        """Executes one step of phonon computation. """
        if (step < 3 * self.beads.natoms):
            self.phcalc.step(step)
        else:
            self.phcalc.transform()
            self.refdynmatrix = apply_asr(self.asr, self.refdynmatrix.copy(), self.beads)
            self.printall(self.prefix, self.refdynmatrix.copy())
            softexit.trigger("Dynamic matrix is calculated. Exiting simulation")

    def printall(self, prefix, dmatx, deltaw=0.0):
        """ Prints out diagnostics for a given dynamical matrix. """

        dmatx = dmatx + np.eye(len(dmatx)) * deltaw
        if deltaw != 0.0:
            wstr = " !! Shifted by %e !!" % (deltaw)
        else:
            wstr = ""
        # prints out the dynamical matrix

        outfile = self.output_maker.get_output(self.prefix + '.dynmat', 'w')
        print >> outfile, "# Dynamical matrix (atomic units)" + wstr
        for i in range(3 * self.beads.natoms):
            print >> outfile, ' '.join(map(str, dmatx[i]))
        outfile.close()

        # also prints out the Hessian
        outfile = self.output_maker.get_output(self.prefix + '.hess', 'w')
        print >> outfile, "# Hessian matrix (atomic units)" + wstr
        for i in range(3 * self.beads.natoms):
            print >> outfile, ' '.join(map(str, dmatx[i] / (self.ism[i] * self.ism)))
        outfile.close()

        # eigsys=np.linalg.eigh(dmatx)
        eigsys = np.linalg.eigh(dmatx)
        # prints eigenvalues & eigenvectors
        outfile = self.output_maker.get_output(self.prefix + '.eigval', 'w')
        print >> outfile, "# Eigenvalues (atomic units)" + wstr
        print >> outfile, '\n'.join(map(str, eigsys[0]))
        outfile.close()
        outfile = self.output_maker.get_output(self.prefix + '.eigvec', 'w')
        print >> outfile, "# Eigenvector  matrix (normalized)"
        for i in range(0, 3 * self.beads.natoms):
            print >> outfile, ' '.join(map(str, eigsys[1][i]))
        outfile.close()

        eigmode = 1.0 * eigsys[1]
        for i in range(0, 3 * self.beads.natoms):
            eigmode[i] *= self.ism[i]
        for i in range(0, 3 * self.beads.natoms):
            eigmode[:, i] /= np.sqrt(np.dot(eigmode[:, i], eigmode[:, i]))
        outfile = self.output_maker.get_output(self.prefix + '.mode', 'w')
        print >> outfile, "# Phonon modes (mass-scaled)"
        for i in range(0, 3 * self.beads.natoms):
            print >> outfile, ' '.join(map(str, eigmode[i]))
        outfile.close()


class DummyPhononCalculator(dobject):

    """ No-op PhononCalculator """

    def __init__(self):
        pass

    def bind(self, dm):
        """ Reference all the variables for simpler access."""
        self.dm = dm

    def step(self, step=None):
        """Dummy simulation time step which does nothing."""
        pass

    def transform(self):
        """Dummy transformation step which does nothing."""
        pass


class FDPhononCalculator(DummyPhononCalculator):

    """ Finite dinnerence phonon evaluator.
    """

    def bind(self, dm):
        """ Reference all the variables for simpler access."""
        super(FDPhononCalculator, self).bind(dm)

        # Initialises a 3*number of atoms X 3*number of atoms dynamic matrix.
        if(self.dm.dynmatrix.size != (self.dm.beads.q.size * self.dm.beads.q.size)):
            if(self.dm.dynmatrix.size == 0):
                self.dm.dynmatrix = np.zeros((self.dm.beads.q.size, self.dm.beads.q.size), float)
            else:
                raise ValueError("Force constant matrix size does not match system size")
        else:
            self.dm.dynmatrix = self.dm.dynmatrix.reshape(((self.dm.beads.q.size, self.dm.beads.q.size)))

        # Initialises a 3*number of atoms X 3*number of atoms refined dynamic matrix.
        if(self.dm.refdynmatrix.size != (self.dm.beads.q.size * self.dm.beads.q.size)):
            if(self.dm.refdynmatrix.size == 0):
                self.dm.refdynmatrix = np.zeros((self.dm.beads.q.size, self.dm.beads.q.size), float)
            else:
                raise ValueError("Force constant matrix size does not match system size")
        else:
            self.dm.refdynmatrix = self.dm.refdynmatrix.reshape(((self.dm.beads.q.size, self.dm.beads.q.size)))

    def step(self, step=None):
        """Computes one row of the dynamic matrix."""

        # initializes the finite deviation
        dev = np.zeros(3 * self.dm.beads.natoms, float)
        dev[step] = self.dm.deltax
        # displaces kth d.o.f by delta.
        self.dm.dbeads.q = self.dm.beads.q + dev
        plus = - dstrip(self.dm.dforces.f).copy()
        # displaces kth d.o.f by -delta.
        self.dm.dbeads.q = self.dm.beads.q - dev
        minus = - dstrip(self.dm.dforces.f).copy()
        # computes a row of force-constant matrix
        dmrow = (plus - minus) / (2 * self.dm.deltax) * self.dm.ism[step] * self.dm.ism
        self.dm.dynmatrix[step] = dmrow
        self.dm.refdynmatrix[step] = dmrow

    def transform(self):
        dm = self.dm.dynmatrix.copy()
        rdm = self.dm.dynmatrix.copy()
        self.dm.dynmatrix = 0.50 * (dm + dm.T)
        self.dm.refdynmatrix = 0.50 * (rdm + rdm.T)


class NMFDPhononCalculator(FDPhononCalculator):

    """ Normal mode finite difference phonon evaluator.
    """

    def bind(self, dm):
        """ Reference all the variables for simpler access."""
        super(NMFDPhononCalculator, self).bind(dm)

        if(np.array_equal(self.dm.dynmatrix, np.zeros((self.dm.beads.q.size, self.dm.beads.q.size)))):
            raise ValueError("Force constant matrix size not found")

        # Initialises a 3*number of atoms X 3*number of atoms refined dynamic matrix.
        if(self.dm.refdynmatrix.size != (self.dm.beads.q.size * self.dm.beads.q.size)):
            if(self.dm.refdynmatrix.size == 0):
                self.dm.refdynmatrix = np.zeros((self.dm.beads.q.size, self.dm.beads.q.size), float)
            else:
                raise ValueError("Refined force constant matrix size does not match system size")

        self.dm.w2, self.dm.U = np.linalg.eigh(self.dm.dynmatrix)
        self.dm.V = self.dm.U.copy()
        for i in xrange(len(self.dm.V)):
            self.dm.V[:, i] *= self.dm.ism

    def step(self, step=None):
        """Computes one row of the dynamic matrix."""

        # initializes the finite deviation
        vknorm = np.sqrt(np.dot(self.dm.V[:, step], self.dm.V[:, step]))
        dev = np.real(self.dm.V[:, step] / vknorm) * self.dm.deltax
        # displaces by -delta along kth normal mode.
        self.dm.dbeads.q = self.dm.beads.q + dev
        plus = - dstrip(self.dm.dforces.f).copy().flatten()
        # displaces by -delta along kth normal mode.
        self.dm.dbeads.q = self.dm.beads.q - dev
        minus = - dstrip(self.dm.dforces.f).copy().flatten()
        # computes a row of the refined dynmatrix, in the basis of the eigenvectors of the first dynmatrix
        dmrowk = (plus - minus) / (2 * self.dm.deltax / vknorm)
        self.dm.refdynmatrix[step] = np.dot(self.dm.V.T, dmrowk)

    def transform(self):
        self.dm.refdynmatrix = np.dot(self.dm.U, np.dot(self.dm.refdynmatrix, np.transpose(self.dm.U)))
        rdm = self.dm.dynmatrix.copy()
        self.dm.refdynmatrix = 0.50 * (rdm + rdm.T)


class ENMFDPhononCalculator(NMFDPhononCalculator):

    """ Energy scaled normal mode finite difference phonon evaluator.
    """

    def step(self, step=None):
        """Computes one row of the dynamic matrix."""

        # initializes the finite deviation
        vknorm = np.sqrt(np.dot(self.dm.V[:, step], self.dm.V[:, step]))
        edelta = vknorm * np.sqrt(self.dm.deltae * 2.0 / abs(self.dm.w2[step]))
        if edelta > 100 * self.dm.deltax: edelta = 100 * self.dm.deltax
        dev = np.real(self.dm.V[:, step] / vknorm) * edelta
        # displaces by -delta along kth normal mode.
        self.dm.dbeads.q = self.dm.beads.q + dev
        plus = - dstrip(self.dm.dforces.f).copy().flatten()
        # displaces by -delta along kth normal mode.
        self.dm.dbeads.q = self.dm.beads.q - dev
        minus = - dstrip(self.dm.dforces.f).copy().flatten()
        # computes a row of the refined dynmatrix, in the basis of the eigenvectors of the first dynmatrix
        dmrowk = (plus - minus) / (2 * edelta / vknorm)
        self.dm.refdynmatrix[step] = np.dot(self.dm.V.T, dmrowk)
