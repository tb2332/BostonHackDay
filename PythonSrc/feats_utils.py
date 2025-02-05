#!/usr/bin/env python
##############################################################
#
# Project by
#               Ron Weis - ronw@ee.columbia.edu
# Thierry Bertin-Mahieux - tb2332@columbia.edu
#
# Find similarity using LSH on some EchoNest features
#
##############################################################

import string
import sys
import os
import os.path
import scipy as SP
import scipy.io
import scipy.signal
#from plottools import plotall as PA
#import matplotlib
#import matplotlib.pyplot as P 
import numpy as N
import glob

try:
    import tables
except ImportError:
    print 'Cannot import pytables'

try:
    from pyechonest import config
    from pyechonest import track

    try:
        config.ECHO_NEST_API_KEY = os.environ['ECHONEST_API_KEY']
    except:
        config.ECHO_NEST_API_KEY = os.environ['ECHO_NEST_API_KEY']

    def get_metadata_from_enid(enid):
        return track.get_metadata(enid)
except ImportError:
    print 'Cannot find pyechonest'


def sample_feats(feats, labels, nsamp=90000):
    randidx = sorted(N.random.permutation(nsamp)[:nsamp])
    smallfeats = N.empty((len(randidx), len(feats[0])))
    smalllabels = []
    for n,x in enumerate(randidx):
        smalllabels.append(str(labels[x]))
        smallfeats[n] = feats[x]
    return smallfeats, smalllabels, randidx

def cluster_feats(feats, labels, ncw=1000, niter=20):
    codebook, quant = SP.cluster.vq.kmeans2(feats, ncw, niter, minit='points')
    # sort codebook by popularity of codeword
    return codebook, quant

def quantize_feats(feats, codebook):
    quant, dist = SP.cluster.vq.vq(feats, codebook)
    return quant, dist

def plot_cluster(memberlabels, matdir, metadir, featlen=16, subplot=(3,3)):
    enids = [str(x).split(':')[0] for x in memberlabels]
    beatidx = [int(str(x).split(':')[1]) for x in memberlabels]
    
    matfiles = [get_matfile_from_enid(matdir, x) for x in enids]
    feats = [SP.io.loadmat(x)['btchroma'][:,bidx:bidx+featlen]
             for x,bidx in zip(matfiles, beatidx)]
    feattimes = [SP.io.loadmat(x)['btstart'].flatten()[bidx]
                 for x,bidx in zip(matfiles, beatidx)]

    meta = [meta_to_dict(SP.io.loadmat(get_matfile_from_enid(metadir, x)))
            for x in enids]
    bardesc = ['%s: %s (%.1f)' % (x['artist'], x['title'], ft)
               for x,ft in zip(meta, feattimes)]

    from plottools import plotall as PA
    import matplotlib
    import matplotlib.pyplot as P 
    PA(feats, title=bardesc, subplot=subplot)
    
    
def meta_to_dict(meta):
    d = dict(artist=None, title=None)
    for row in meta['data']:
        d[row[0][0][:-1]] = row[1][0]
    return d
    

def write_features_into_h5(h5filename, matfiles, barfeats_params):
    h5file = tables.openFile(h5filename, mode='a')

    h5file.createArray('/', 'matfiles', matfiles)
    h5file.createArray('/', 'barfeats_params', '%s' % barfeats_params)
    
    feats,labels = matfile_to_barfeats(matfiles[0], **barfeats_params)
    featdim = feats.shape[0]
    h5feats = h5file.createEArray('/', 'feats',
                                  tables.Float32Atom(shape=(featdim,)),
                                  (0,),
                                  expectedrows=100 * len(matfiles))
    ENID_LEN = 18
    BARID_LEN = ENID_LEN + 5
    h5labels = h5file.createEArray('/', 'labels',
                                   tables.StringAtom(itemsize=BARID_LEN),
                                   (0,),
                                   expectedrows=100 * len(matfiles))

    for x in matfiles:
        feats, labels = matfile_to_barfeats(x, **barfeats_params)
        for n in xrange(len(labels)):
            h5feats.append(feats[:,n])
            h5labels.append(N.array([labels[n]], dtype=h5labels.atom.dtype))

    h5file.close()


def read_features_from_h5(h5filename):
    f = tables.openFile(h5filename, 'r')
    feats = f.getNode('/feats')
    labels = f.getNode('/labels')
    return feats, labels


def resample(data, newsize):
    """ resample the data, columnwise """
    if newsize > 1:
        return SP.signal.resample(data, newsize, axis=1)
    # special case, newsize == 1
    if newsize == 1 and data.shape[1] == 1:
        return data
    return N.mean(data,axis=1).reshape(data.shape[0],1)

def matfile_to_enid(matfile):
    """Convert matfilename to an echo nest track id."""
    return os.path.split(matfile)[-1].replace('.mat', '').upper()



def keyinvariance_maxenergy(pattern,retRoll=False):
    """
    A different way to try to be key invariant from the FFT.
    Important feature: the relative pitch of the events must be
    unchanged.
    We compute the row with the max energy, and we rotate so it
    row 0.
    If retRoll == True, we also return the roll. To get back
    the original pattern, apply -roll on axis=0
    """
    # find max row
    max_r = N.argmax(N.sum(pattern,axis=1))
    # roll
    if not retRoll:
        return N.roll(pattern,pattern.shape[0]-max_r,axis=0)
    roll = pattern.shape[0]-max_r
    return N.roll(pattern,roll,axis=0),roll


def downbeatinvariance_maxenergy(pattern,retRoll=False):
    """
    A different way to try to be key invariant from the FFT.
    Important feature: notes played at the same time remain
    together.
    We compute the column with the max energy, and we rotate
    so it is column 0.
    If retRoll == True, we also return the roll. To get back
    the original pattern, apply -roll on axis=0
    """
    # find max row
    max_c = N.argmax(N.sum(pattern,axis=0))
    # roll
    if not retRoll:
        return N.roll(pattern,pattern.shape[1]-max_c,axis=1)
    roll = pattern.shape[1]-max_c
    return N.roll(pattern,roll,axis=1),roll

def normalize_pattern_maxenergy(pattern, newsize=16, keyinvariant=False,
                                downbeatinvariant=False,retRoll=False):
    """Take a pattern, a matrix 12xN, resize it to the right length
    and applies the invariant.
    Can be applied to the output of a DataIterator
    Uses energy to make the pattern invariant.

    If retRool true, returns three things: pattern, key invariance,
    downbeat invariance. Those last two are zero if nothing happens.
    """
    if not retRoll:
        if (not keyinvariant) and (not downbeatinvariant):
            return resample(pattern,newsize)
        if keyinvariant and (not downbeatinvariant):
            return keyinvariance_maxenergy(resample(pattern,newsize))
        if (not keyinvariant) and downbeatinvariant:
            return downbeatinvariance_maxenergy(resample(pattern,newsize))
        else:
            return downbeatinvariance_maxenergy(
                keyinvariance_maxenergy(resample(pattern,newsize)))
    # case where we return roll too
    if (not keyinvariant) and (not downbeatinvariant):
        return resample(pattern,newsize),0,0
    if keyinvariant and (not downbeatinvariant):
        p,roll =  keyinvariance_maxenergy(resample(pattern,newsize),retRoll=True)
        return p,roll,0
    if (not keyinvariant) and downbeatinvariant:
        p,roll = downbeatinvariance_maxenergy(resample(pattern,newsize),retRoll=True)
        return p,0,roll
    else:
        p,keyroll = keyinvariance_maxenergy(resample(pattern,newsize),retRoll=True)
        p,dbroll = downbeatinvariance_maxenergy(p,retRoll=True)
        return p,keyroll,dbroll
    

def normalize_pattern(pattern, newsize=16, keyinvariant=False,
                      downbeatinvariant=False):
    """Take a pattern, a matrix 12xN, resize it to the right length
    and applies the invariant. Used by matfile_to_barfeats, and
    can be applied to the output of a DataIterator"""
    if keyinvariant and downbeatinvariant:
        invariance_fun = lambda bar: N.abs(N.fft.rfft2(bar))
    elif keyinvariant:
        invariance_fun = lambda bar: N.abs(N.fft.rfft(bar, axis=0))
    elif downbeatinvariant:
        invariance_fun = lambda bar: N.abs(N.fft.rfft(bar, axis=1))
    else:
        invariance_fun = lambda bar: bar
    # apply transform
    return invariance_fun(resample(pattern,newsize))

def matfile_to_barfeats(matfile, newsize=16, keyinvariant=False,
                        downbeatinvariant=False, barsperfeat=1):
    """Convert beat-synchronous chroma features from matfile to a set
    of fixed length chroma features for every bar."""
    mat = read_matfile(matfile)
    try:
        chroma = mat['btchroma']
        bars = mat['barbts'].flatten()
    except:
        print 'problem with file: ' + matfile
        return N.array([]),N.array([])

    #if keyinvariant and downbeatinvariant:
    #    invariance_fun = lambda bar: N.abs(N.fft.rfft2(bar))
    #elif keyinvariant:
    #    invariance_fun = lambda bar: N.abs(N.fft.rfft(bar, axis=0))
    #elif downbeatinvariant:
    #    invariance_fun = lambda bar: N.abs(N.fft.rfft(bar, axis=1))
    #else:
    #    invariance_fun = lambda bar: bar

    barfeats = []
    for n in xrange(0, len(bars), barsperfeat):
        try:
            end = bars[n+barsperfeat]
        except IndexError:
            end = chroma.shape[1]
        feat = normalize_pattern(chroma[:,bars[n]:end],newsize*barsperfeat,
                                 keyinvariant,downbeatinvariant)
        barfeats.append(feat.flatten())

    enid = matfile_to_enid(matfile)
    barlabels = ['%s:%d' % (enid, x) for x in bars]

    return N.asarray(barfeats).T, barlabels


def matfiles_to_feats_to_txt(matfiles,featfile,descfile, newsize=16,
                             keyinvariant=False,
                             downbeatinvariant=False, barsperfeat=1):
    """Take all matlab files, get barfeats, output the results
    into 2 text files, one for features and one for description.
    There's one bar per line, e.g. both file should have as many
    lines. The feat file format fits with E2LSH input format."""

    fidFeat = open(featfile,'w')
    fidDesc = open(descfile,'w')
    # iterate over matfiles
    for matfile in matfiles :
        barfeats, barlabels = matfile_to_barfeats(matfile,newsize,
                                                  keyinvariant,
                                                  downbeatinvariant,
                                                  barsperfeat)
        # iterate over beats
        if barfeats.shape[0] < 1 :
            print 'problem with file: ' + matfile
            continue
        for n in range(barfeats.shape[1]) :
            # write features
            try:
                barfeats[:,n].tofile(fidFeat,sep=' ')
            except:
                continue
            fidFeat.write('\n')
            # write descriptions
            #fidDesc.write(barlabels[(n-1)*newsize])
            fidDesc.write(barlabels[n])
            fidDesc.write('\n')
    # close files, and done
    fidFeat.close()
    fidDesc.close()


def get_all_matfiles(basedir) :
    """From a root directory, go through all subdirectories
    and find all matlab files. Return them in a list."""
    allfiles = []
    for root, dirs, files in os.walk(basedir):
        matfiles = glob.glob(os.path.join(root,'*.mat'))
        for f in matfiles :
            allfiles.append( os.path.abspath(f) )
    return allfiles


def get_matfile_from_enid(basedir, enid):
    """From a root directory, go through all subdirectories
    until a matfile that fits the Echno Nest id is found.
    Return the absolute path. Returns an empty string if not
    found."""
    target = enid.lower() + '.mat'
    for root, dirs, files in os.walk(basedir):
        localtarget = os.path.join(root,target)
        if os.path.isfile(localtarget) :
            return os.path.abspath(localtarget)
    print 'matfile for enid:' + enid + ' not found, basedir:' + basedir
    return ''



def read_feat_file(filename,sep=' ') :
    """ we read a file of features, one example per line,
    same number of features per example. Returns a numpy
    array."""
    # count lines
    cntlines = 0
    fid = open(filename,'r')
    for line in fid.xreadlines():
        if line == '' or line.strip() == '':
            continue
        cntlines = cntlines + 1
    fid.close()
    # count features
    fid = open(filename,'r')
    for line in fid.xreadlines():
        if line == '' or line.strip() == '':
            continue
        numfeats = len(line.strip().split(sep))
        break
    fid.close()
    # init big array
    result = N.zeros([cntlines,numfeats])
    # read!
    fid = open(filename,'r')
    cnt = 0
    for line in fid.xreadlines():
        if line == '' or line.strip() == '':
            continue
        result[cnt,:] = N.asarray(line.strip().split(sep))
        cnt = cnt + 1
    fid.close()
    # return
    return result
    



def imshow(data) :
    """
    Wrapper around matplotlib.pyplot with proper params
    """
    from plottools import plotall as PA
    import matplotlib
    import matplotlib.pyplot as P 
    PA([data],
       aspect='auto', interpolation='nearest')
    P.show()


def read_matfile(filename):
    """
    Read a matlabfile
    Uses scipy utility function
    TODO: load differently based on python version
    """
    try:
        return SP.io.loadmat(filename)
    except:
        return dict()



def die_with_usage():
    """
    Help Menu
    """
    print 'feats_utils.py'
    print 'a set of functions to get features, plot them,'
    print 'and transform them'
    print 'goal: similarity through LSH'
    sys.exit(0)




##############################################################
# MAIN
##############################################################
if __name__ == '__main__' :

    if len(sys.argv) < 2 :
        die_with_usage()


    print('dummy tests!')

    # load and plot
    data = read_matfile('../tr0002q11c3fa8332d.mat')
    chromas = data['btchroma']
    beats = data['barbts']
    P.figure()
    imshow(chromas)

    # resample and show
    chromas2 = resample(chromas,50)
    P.figure()
    imshow(chromas2)
