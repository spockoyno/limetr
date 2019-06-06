# nonlinear mixed effects model
import numpy as np
import ipopt
from limetr.utils import VarMat
from limetr.utils import projCappedSimplex


class LimeTr:
    def __init__(self, n, k_beta, k_gamma, Y, F, JF, Z, S,
                 C=None, JC=None, c=None,
                 H=None, JH=None, h=None,
                 uprior=None, gprior=None,
                 inlier_percentage=1.0):
        # pass in the dimension
        self.n = np.array(n)
        self.m = len(n)
        self.N = sum(n)
        self.k_beta = k_beta
        self.k_gamma = k_gamma
        self.k = self.k_beta + self.k_gamma

        self.idx_beta = slice(0, self.k_beta)
        self.idx_gamma = slice(self.k_beta, self.k)
        self.idx_split = np.cumsum(np.insert(n, 0, 0))[:-1]

        # pass in the data
        self.Y = Y
        self.F = F
        self.JF = JF
        self.Z = Z
        self.S = S
        self.V = S**2

        # pass in the priors
        self.use_constraints = (C is not None)
        self.use_regularizer = (H is not None)
        self.use_uprior = (uprior is not None)
        self.use_gprior = (gprior is not None)

        self.C = C
        self.JC = JC
        self.c = c
        if self.use_constraints:
            self.constraints = C
            self.jacobian = JC
            self.num_constraints = C(np.zeros(self.k)).size
            self.cl = c[0]
            self.cu = c[1]
        else:
            self.num_constraints = 0
            self.cl = None
            self.cu = None

        self.H = H
        self.JH = JH
        self.h = h
        if self.use_regularizer:
            self.num_regularizer = H(np.zeros(self.k)).size
        else:
            self.num_regularizer = 0

        if self.use_uprior:
            self.uprior = uprior
        else:
            self.uprior = np.array([
                [-np.inf]*self.k_beta + [0.0]*self.k_gamma,
                [np.inf]*self.k_beta + [np.inf]*self.k_gamma
                ])
            self.use_uprior = True

        self.lb = self.uprior[0]
        self.ub = self.uprior[1]

        if self.use_gprior:
            self.gprior = gprior

        # trimming option
        self.use_trimming = (0.0 < inlier_percentage < 1.0)
        self.inlier_percentage = inlier_percentage
        self.num_inliers = np.floor(inlier_percentage*self.N)
        self.num_outliers = self.N - self.num_inliers
        self.w = np.repeat(self.num_inliers/self.N, self.N)

        # specify solution to be None
        self.soln = None
        self.info = None
        self.beta = np.zeros(self.k_beta)
        self.gamma = np.repeat(0.01, self.k_gamma)

        # check the input
        self.check()

    def check(self):
        assert self.Y.shape == (self.N,)
        assert self.S.shape == (self.N,)
        assert self.Z.shape == (self.N, self.k_gamma)
        assert np.all(self.S > 0.0)

        if self.use_constraints:
            assert self.c.shape == (2, self.num_constraints)
            assert np.all(self.cl <= self.cu)

        if self.use_regularizer:
            assert self.h.shape == (2, self.num_regularizer)
            assert np.all(self.h[1] > 0.0)

        if self.use_uprior:
            assert np.all(self.lb <= self.ub)

        if self.use_gprior:
            assert self.gprior.shape == (2, self.k)
            assert np.all(self.gprior[1] > 0.0)

        assert 0.0 < self.inlier_percentage <= 1.0

    def objective(self, x, use_ad=False):
        # unpack variable
        beta = x[self.idx_beta]
        gamma = x[self.idx_gamma]

        # trimming option
        if self.use_trimming:
            sqrt_w = np.sqrt(self.w)
            sqrt_W = sqrt_w.reshape(self.N, 1)
            F_beta = self.F(beta)*sqrt_w
            Y = self.Y*sqrt_w
            Z = self.Z*sqrt_W
            V = self.V**self.w
        else:
            F_beta = self.F(beta)
            Y = self.Y
            Z = self.Z
            V = self.V

        # residual and variance
        R = Y - F_beta
        D = VarMat(V, Z, gamma, self.n)

        val = 0.5*self.N*np.log(2.0*np.pi)

        if use_ad:
            # should only use when testing
            varmat = D
            D = varmat.varMat()
            inv_D = varmat.invVarMat()

            val += 0.5*np.log(np.linalg.det(D))
            val += 0.5*R.dot(inv_D.dot(R))
        else:
            val += 0.5*D.logDet()
            val += 0.5*R.dot(D.invDot(R))

        # add gpriors
        if self.use_regularizer:
            val += 0.5*np.sum(((self.H(x) - self.h[0])/self.h[1])**2)

        if self.use_gprior:
            val += 0.5*np.sum(((x - self.gprior[0])/self.gprior[1])**2)

        return val

    def gradient(self, x, use_ad=False, eps=1e-12):
        if use_ad:
            # should only use when testing
            g = np.zeros(self.k)
            z = x + 0j
            for i in range(self.k):
                z[i] += eps*1j
                g[i] = self.objective(z, use_ad=use_ad).imag/eps
                z[i] -= eps*1j

            return g

        # unpack variable
        beta = x[self.idx_beta]
        gamma = x[self.idx_gamma]

        # trimming option
        if self.use_trimming:
            sqrt_w = np.sqrt(self.w)
            sqrt_W = sqrt_w.reshape(self.N, 1)
            F_beta = self.F(beta)*sqrt_w
            JF_beta = self.JF(beta)*sqrt_W
            Y = self.Y*sqrt_w
            Z = self.Z*sqrt_W
            V = self.V**self.w
        else:
            F_beta = self.F(beta)
            JF_beta = self.JF(beta)
            Y = self.Y
            Z = self.Z
            V = self.V

        # residual and variance
        R = Y - F_beta
        D = VarMat(V, Z, gamma, self.n)

        # gradient for beta
        g_beta = -JF_beta.T.dot(D.invDot(R))

        # gradient for gamma
        DZ = D.invDot(Z)
        g_gamma = 0.5*np.sum(Z*DZ, axis=0) -\
            0.5*np.sum(
                np.add.reduceat(DZ.T*R, self.idx_split, axis=1)**2,
                axis=1)

        g = np.hstack((g_beta, g_gamma))

        # add gradient from the regularizer
        if self.use_regularizer:
            g += self.JH(x).T.dot((self.H(x) - self.h[0])/self.h[1]**2)

        # add the gradient from the gprior
        if self.use_gprior:
            g += (x - self.gprior[0])/self.gprior[1]**2

        return g

    def objectiveTrimming(self, w):
        t = (self.Z**2).dot(self.gamma)
        r = (self.Y - self.F(self.beta))**2
        v = self.V**w
        d = v + t*w

        val = 0.5*np.sum(r*w/d)
        val += 0.5*self.N*np.log(2.0*np.pi) + 0.5*np.sum(np.log(d))
        
        return val

    def gradientTrimming(self, w, use_ad=False, eps=1e-10):
        if use_ad:
            # only use when testing
            g = np.zeros(self.N)
            z = w + 0j
            for i in range(self.N):
                z[i] += eps*1j
                g[i] = self.objectiveTrimming(z).imag/eps
                z[i] -= eps*1j

            return g

        t = (self.Z**2).dot(self.gamma)
        r = (self.Y - self.F(self.beta))**2
        v = self.V**w
        d = v + t*w

        g = 0.5*r*v*(1.0 - np.log(v))/d**2
        g += 0.5*(t + v*np.log(self.V))/d

        return g

    def optimize(self, x0=None, print_level=0, max_iter=100):
        if x0 is None:
            x0 = np.hstack((self.beta, self.gamma))

        assert x0.size == self.k

        opt_problem = ipopt.problem(
            n=self.k,
            m=self.num_constraints,
            problem_obj=self,
            lb=self.uprior[0],
            ub=self.uprior[1],
            cl=self.cl,
            cu=self.cu
            )

        opt_problem.addOption('print_level', print_level)
        opt_problem.addOption('max_iter', max_iter)

        self.soln = soln
        self.info = info
        self.beta = soln[self.idx_beta]
        self.gamma = soln[self.idx_gamma]

    def fitModel(self, x0=None,
                 inner_print_level=0,
                 inner_max_iter=20,
                 outer_verbose=False,
                 outer_max_iter=100,
                 outer_step_size=1.0,
                 outer_tol=1e-6):

        if not self.use_trimming:
            self.optimize(x0=x0,
                          print_level=inner_print_level,
                          max_iter=inner_max_iter*outer_max_iter)

            return self.beta, self.gamma, self.w

        num_iter = 0
        err = outer_tol + 1.0

    @classmethod
    def testProblem(cls,
                    use_trimming=False,
                    use_constraints=False,
                    use_regularizer=False,
                    use_uprior=False,
                    use_gprior=False):
        m = 10
        n = [5]*m
        N = sum(n)
        k_beta = 3
        k_gamma = 2
        k = k_beta + k_gamma

        beta_t = np.random.randn(k_beta)
        gamma_t = np.random.rand(k_gamma)*0.09 + 0.01

        X = np.random.randn(N, k_beta)
        Z = np.random.randn(N, k_gamma)

        S = np.random.rand(N)*0.09 + 0.01
        V = S**2
        D = np.diag(V) + (Z*gamma_t).dot(Z.T)

        U = np.random.multivariate_normal(np.zeros(N), D)
        E = np.random.randn(N)*S

        Y = X.dot(beta_t) + U + E

        def F(beta, X=X):
            return X.dot(beta)

        def JF(beta, X=X):
            return X

        # constraints, regularizer and priors
        if use_constraints:
            M = np.ones((1, k))

            def C(x, M=M):
                return M.dot(x)

            def JC(x, M=M):
                return M

            c = np.array([[0.0], [1.0]])
        else:
            C, JC, c = None, None, None

        if use_regularizer:
            M = np.ones((1, k))

            def H(x, M=M):
                return M.dot(x)

            def JH(x, M=M):
                return M

            h = np.array([[0.0], [2.0]])
        else:
            H, JH, h = None, None, None

        if use_uprior:
            uprior = np.array([[0.0]*k, [np.inf]*k])
        else:
            uprior = None

        if use_gprior:
            gprior = np.array([[0.0]*k, [2.0]*k])
        else:
            gprior = None

        if use_trimming:
            inlier_percentage = 0.9
        else:
            inlier_percentage = 1.0

        return cls(n, k_beta, k_gamma, Y, F, JF, Z, S,
                   C=C, JC=JC, c=c,
                   H=H, JH=JH, h=h,
                   uprior=uprior, gprior=gprior,
                   inlier_percentage=inlier_percentage)
