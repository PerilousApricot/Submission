#!/bin/env python

import binConfig
import checkEnvironment
from datetime import datetime
import optparse,os,time,pickle,subprocess,shutil,sys,getpass
import logging
import ROOT
log = logging.getLogger( 'remote' )


def bins(input_file, bin_size):

    binning_list = []
    size_list = []
    name_list = []

    f = open(input_file,"r")
    for item in f:
        tmp_line = item.strip().split()
        size_list.append(int(tmp_line[0]))
        name_list.append(tmp_line[1])

    binning_size = []
    maxsize=0
    current_bin=[]
    for i, sizer in enumerate(size_list):
        current_bin.append(name_list[i])
        maxsize+=sizer
        # one file per bin
        if maxsize>bin_size or (maxsize+sizer*0.5)>bin_size:
            #print(maxsize,current_bin)
            binning_list.append(current_bin)
            current_bin=[]
            maxsize=0
        elif i==len(size_list)-1:
            binning_list.append(current_bin)
            
    return binning_list


def getFolder(folder,sample):
    import glob
    folders=glob.glob(folder)
    # get the newest!!
    folder=None
    if len(folders)==1:
        folder=folders[0]
    else:
        newest=None
        log.info("Sample %s is not unique, will use"%sample)
        folder=None
        for ifold in folders:
            if newest == None:
                newest = os.path.getmtime(ifold)
                folder=ifold
            elif os.path.getmtime(ifold)> newest:
                newest = os.path.getmtime(ifold)
                folder=ifold
        for f in folders:
            print f.split("/")[-1]
            if f.split("/")[-1]==sample:
                print f
                folder=f
        log.info(folder)
    return folder
    

def create_sample_list(sample):
    log.info("Will get the file list for %s"%sample)
    
    locations=[
    "/eos/uscms/store/user/ra2tau/July72017/*/%s"%(sample),
    "/eos/uscms/store/user/ra2tau/July72017/*/*%s*"%(sample),
    "/eos/uscms/store/user/ra2tau/jan2017tuple/*/*%s*"%(sample),
    "/eos/uscms/store/user/ra2tau/jan2017tuple/*/%s"%(sample),
    "/eos/uscms//store/user/jruizalv/*/*/*%s*"%(sample),
    ]
    for l in locations:
        folder=getFolder(l,sample)
        if folder is not None and folder!="":
            break
    if folder is None:
        log.info("Sample %s was not found"%sample)
    
    sampleFile = open("list_Samples/" + sample + ".txt","w")
    rfile=ROOT.TFile()
    for root, dirs, files in os.walk(folder):
        if len(files)>0:
            testcommand="./test_root %s/*.root"%root
            
            #p = subprocess.Popen(testcommand, shell=True, stdout=subprocess.PIPE,
                           #stderr=subprocess.PIPE,
                           #stdin=subprocess.PIPE)
            #out, err = p.communicate()
            #output=err
            try:
                output=subprocess.check_output(testcommand,shell=True,stderr=subprocess.STDOUT)
            except:
                output=""
            for f in files:
                if ".root" in f:
                    filepath=os.path.join(root, f).replace("/eos/uscms","root://cmseos.fnal.gov//")
                    if filepath.split("//")[-1] in output:
                        log.info("Bad file: %s"%filepath)
                        continue
                    
                    sampleFile.write("%d     %s\n"%(os.path.getsize(os.path.join(root, f)), filepath ))
    sampleFile.close()

def getFilesfromFile(cfgFile, options):
    sampleList={}
    file = open(cfgFile,'r')
    if(not os.path.exists("list_Samples")):
        os.makedirs("list_Samples")
    
    for line in file:
        if line[0]=="#" or len(line.split())==0:
            continue
        sample=line.strip()
        if os.path.exists("list_Samples/"+sample+".txt"):
            file_lists=bins("list_Samples/"+sample+".txt",3000000000)#size in bytes 1GB
        else:
            create_sample_list(sample)
            file_lists=bins("list_Samples/"+sample+".txt",3000000000)#size in bytes 1GB
        if len(file_lists)>0:
            file_lists=[x for x in file_lists if "failed" not in x]
            sampleList[sample]=file_lists
    return sampleList

def makeExe(options,inputfiles,outputfile,sample):
    from string import Template
    exe="""
#!/bin/bash -e
sleep $[ ( $RANDOM % 30 ) ]
date

cd ${_CONDOR_SCRATCH_DIR}
"""
    for fileDir in binConfig.cpFiles:
        exe+="cp -r $ANALYSISDIR/"+fileDir+" .\n"

    exe+="""
cp -r $ANALYSISDIR/$CONFIGDIR .

isData=$ISDATA

if [ ! -z $isData ]
then
    sed -r -i -e 's/(isData\s+)(0|false)/true/' -e 's/(CalculatePUS[a-z]+\s+)(1|true)/false/' \
	$CONFIGDIR/Run_info.in
else
    sed -r -i -e 's/(isData\s+)(1|true)/false/' -e 's/(CalculatePUS[a-z]+\s+)(0|false)/true/' \
	$CONFIGDIR/Run_info.in
fi

./Analyzer -in $INPUTFILES -out $OUPUTFILE -C $CONFIGDIR $CONTOLLREGION

xrdcp -sf $_CONDOR_SCRATCH_DIR/$OUPUTFILE $OUTPUTFOLDER/$SAMPLE/$OUPUTFILE
rm run.sh
"""

    for fileDir in binConfig.cpFiles:
        exe+="rm -r "+fileDir+" \n"
    exe+="rm -r $CONFIGDIR \n"
    exe+="rm -r *.root \n"
    CR=""
    if options.CR:
        CR="-CR"
    d = dict(
            ANALYSISDIR=binConfig.PathtoExecutable.replace("/uscms/home/","/uscms_data/d3/").replace("nobackup/",""),
            CONFIGDIR=options.configdir,
            INPUTFILES=" ".join(inputfiles),
            OUPUTFILE=outputfile,
            OUTPUTFOLDER=options.outputFolder,
            SAMPLE=sample,
            CONTOLLREGION=CR,
        )
    exe=Template(exe).safe_substitute(d)
    exeFile=open("run_"+outputfile.replace(".root","")+".sh","w+")
    exeFile.write(exe)
    exeFile.close()

def main():

    date_time = datetime.now()
    usage = '%prog [options] CONFIG_FILE'
    parser = optparse.OptionParser( usage = usage )
    parser.add_option( '-C', '--configdir', default = "PartDet", metavar = 'DIRECTORY',
                            help = 'Define the config directory. [default = %default]')
    parser.add_option( '-c', '--CR', action = 'store_true', default = False,
                            help = 'Run with the CR flag. [default = %default]')
    parser.add_option( '-o', '--outputFolder', default = "root://cmseos.fnal.gov//store/user/%s/REPLACEBYTAG/"%(getpass.getuser()), metavar = 'DIRECTORY',
                            help = 'Define path for the output files [default = %default]')
    parser.add_option( '-l', '--local',action = 'store_true', default = False,
                            help = 'run localy over the files [default = %default]')
    parser.add_option( '-f', '--force',action = 'store_true', default = False,
                            help = 'Force the output folder to be overwritten. [default = %default]')
    parser.add_option( '--debug', metavar = 'LEVEL', default = 'INFO',
                       help= 'Set the debug level. Allowed values: ERROR, WARNING, INFO, DEBUG. [default = %default]' )
    parser.add_option( '-t', '--Tag', default = "output%s_%s_%s"%(date_time.year,
                                                                        date_time.month,
                                                                        date_time.day), metavar = 'DIRECTORY',
                        help = 'Define a Tag for the output directory. [default = %default]' )
        

    ( options, args ) = parser.parse_args()
    if len( args ) != 1:
        parser.error( 'Exactly one CONFIG_FILE required!' )
    options.outputFolder=options.outputFolder.replace("REPLACEBYTAG",options.Tag)



    format = '%(levelname)s from %(name)s at %(asctime)s: %(message)s'
    date = '%F %H:%M:%S'
    logging.basicConfig( level = logging._levelNames[ options.debug ], format = format, datefmt = date )
    log.info("Welcome to the wonders of color!")

    try:
       cmssw_version, cmssw_base, scram_arch = checkEnvironment.checkEnvironment()
    except EnvironmentError as err:
        log.error( err )
        log.info( 'Exiting...' )
        sys.exit( err.errno )


    if os.path.exists(options.outputFolder.replace("root://cmseos.fnal.gov/","/eos/uscms/")) and not options.force:
        log.error("The outpath "+options.outputFolder+" already exists pick a new one or use --force")
        sys.exit(3)
    elif options.force and os.path.exists(options.outputFolder.replace("root://cmseos.fnal.gov/","/eos/uscms/")):
        shutil.rmtree(options.outputFolder.replace("root://cmseos.fnal.gov/","/eos/uscms/"))
    os.makedirs(options.outputFolder.replace("root://cmseos.fnal.gov/","/eos/uscms/"))
    
    
    cfgFile = args[ 0 ]
    sampleList=getFilesfromFile(cfgFile,options)
    

    thisdir=os.getcwd()


    n_jobs=0
    for sample in sampleList:
        n_jobs+=len(sampleList[sample])
    print(("There will be %d jobs in total"%n_jobs))

    sbumittedjobs=0
    for sample in sampleList:
        os.chdir(thisdir)
        if os.path.exists(os.path.join(options.Tag,sample)) and not options.force:
            log.error("The samplepath "+os.path.join(options.Tag,sample)+" already exists use the --force")
            sys.exit(3)
        elif options.force and os.path.exists(os.path.join(options.Tag,sample)):
            shutil.rmtree(os.path.join(options.Tag,sample))
        os.makedirs(os.path.join(options.Tag,sample))
        os.chdir(os.path.join(options.Tag,sample))
        ## I know not the way we want to trigger stuff
        wrapper="""#!/bin/bash -e
cp $@ run.sh
chmod u+x run.sh
./run.sh
        """
        condor_jdl="executable      = "+os.path.join(os.getcwd(),"wrapper.sh")+"\n"
        condor_jdl+="""
universe        = vanilla
getenv          = true
Error           = err.$(Process)_$(Cluster)
Output          = out.$(Process)_$(Cluster)
Log             = condor_$(Cluster).log
request_memory  = 0.5 GB
Notification    = Error

"""
        f=open("wrapper.sh","w")
        f.write(wrapper)
        f.close()
        
        for i,binned in enumerate(sampleList[sample]):
            makeExe(options,binned,"%s_%d.root"%(sample,i),sample)
            condor_jdl+="arguments = %s \n"%(os.path.join(os.getcwd(),"run_%s_%d.sh"%(sample,i)) )
            condor_jdl+="queue\n"
        condor_jdl+="\n"
        f=open("condor.jdl","w")
        f.write(condor_jdl)
        f.close()
        log.info("Submitting sample %s"%sample)
        command="condor_submit condor.jdl"
        log.debug(command)
        subprocess.call(command, shell=True)
    
    os.chdir(thisdir)
    legacy_file=open("submitted_samples.txt","w")
    legacy_file.write("outFolder:%s\n"%options.outputFolder.replace("root://cmseos.fnal.gov/","/eos/uscms/"))
    for sample in sampleList:
        legacy_file.write("%s\n"%(sample))
    legacy_file.close()
    log.info("Thanks for zapping in, bye bye")
    log.info("The out files will be in "+options.outputFolder)
    log.info("Check the status with condor_q %s"%(getpass.getuser()))
    log.info("When finished run ./add_root_files.py")

if __name__ == '__main__':
    main()