#!/bin/env python


import logging
log = logging.getLogger( "merger" )

import optparse
import os
import multiprocessing
import re,sys,time,shutil,glob,subprocess
from random import randint
from datetime import datetime
import re
os.nice(10)

# main function is called at the end.
#
def main():
    date_time = datetime.now()
    usage = '%prog [options]'
    parser = optparse.OptionParser( usage = usage )
    parser.add_option( '-i','--inputFolder' , metavar = 'FOLDER', default=None,
                       help = 'Merge all subfolders in these folders, which can be a comma-separated list.[default: %default]' )
    parser.add_option( '--debug', metavar = 'LEVEL', default = 'INFO',
                       help= 'Set the debug level. Allowed values: ERROR, WARNING, INFO, DEBUG. [default = %default]' )
    parser.add_option( '-o','--output', metavar= "OUTFOLDER", default="output%s_%s_%s_%s_%s"%(date_time.year,
                                                                        date_time.month,
                                                                        date_time.day,
                                                                        date_time.hour,
                                                                        date_time.minute),
                         help= 'Set the output dir [default = %default]' )
    parser.add_option( '-f', '--force',action = 'store_true', default = False,
                             help = 'If this option is specifed, all root files will be remerged. [default = %default]' )
    parser.add_option( '-c', '--clean',action = 'store_true', default = False,
                             help = 'If this option is specifed, the folders will be cleand up. [default = %default]' )
    parser.add_option( '-k', '--keep',metavar= 'keep', default = "METParked",
                             help = 'Folders with this name will be kept  [default = %default]' )
    parser.add_option( '--veto',metavar= 'veto', default = None,
                             help = 'Veto a specific sample e.g. Tau_Run20  [default = %default]' )
    parser.add_option( '--hadd',metavar= 'hadd', default = "fk",
                             help = 'hadd options (will be added with a - to the hadd) use like --hadd="fk O" [default = %default]' )
    parser.add_option( '--dryrun',action = 'store_true', default = False,
                             help = 'make a run without executing the hadd [default = %default]' )
    
    ( options, args ) = parser.parse_args()

    format = '%(levelname)s %(name)s (%(asctime)s): %(message)s'
    date = '%H:%M:%S'
    logging.basicConfig( level = logging._levelNames[ options.debug ], format = format, datefmt = date )
    
    if (options.inputFolder==None and not os.path.exists("submitted_samples.txt")):
        log.error( "You must give either a input file or keep the submitted_samples.txt" )
        sys.exit(3)
    if (options.inputFolder==None and os.path.exists("submitted_samples.txt")):
        f=open("submitted_samples.txt","r")
        for line in f:
            if "outFolder:" in line:
                options.inputFolder=line.replace("outFolder:","").strip()
                break
    if len(options.hadd.split())>1:
        options.hadd=" -".join(options.hadd.split(" "))
    options.hadd=" -"+options.hadd
    print options.output
    megeRootFiles(options)
    
    final_merge(options)
    if options.clean:
        cleanUp(options)
    write_info(options)
        
def write_info(options):
    info_file=open(os.path.join(options.output,"info.txt"),"w")
    info_file.write("This is the output for the run folder:\n")
    info_file.write(options.inputFolder+"\n")
    info_file.close()

def final_merge(options):
    files=glob.glob(options.output+"/tmp/*.root")
    short_names=set()
    match1 = re.compile('(_ext)|(_Tune)|([_-][vV][123])|(_RunII)|(_13TeV)')
    
    for file in files:
        if(os.path.isdir(file)):
            continue
        file=os.path.basename(file)
        file=file.replace("crab_","")
        if "Run20" in file:
            continue
        m1=match1.search(file)
        if m1 is not None:
            shortname=file[0:m1.span()[0]]
            short_names.add(shortname)
        else:
            short_names.add(file.replace(".root",""))
    outdir=options.output
    if not os.path.exists(outdir):
        os.makedirs(outdir)
    args=[]
    for f in short_names:
        outputname=os.path.join(outdir,f+".root")
        samplelist=glob.glob(outdir+"/tmp/*%s*.root"%f)
        samplelist=filter(lambda x: (f+"0" not in x) and (f+"_HT" not in x),samplelist)
        samplelist=filter(lambda x: ("/"+f in x),samplelist)
        if len(samplelist)==1 and samplelist[0]==outputname:
            continue
        args.append([outputname, f, samplelist, options])
    if len(args)>0:
        #now merge all samples
        pool = multiprocessing.Pool(10)
        pool.map_async(hadd, args)
        while True:
            time.sleep(5)
            if not pool._cache: break
    if os.path.exists(options.output+"/tmp/allData.root"):
        shutil.move(options.output+"/tmp/allData.root",options.output+"/allData.root")
    
    

def megeRootFiles(options):
    replaceItems=[]
    listOfSamples=[]

    outputFolder=options.output+"/tmp"
    if not os.path.exists(outputFolder):
        os.makedirs(outputFolder)
    for sample in glob.glob(options.inputFolder+"/*"):
        if options.veto is not None:
            if options.veto in sample:
                continue
        samplelist=glob.glob(sample+"/*.root")
        for s in range(len(samplelist)):
            samplelist[s]=samplelist[s].replace("/eos/uscms//","root://cmseos.fnal.gov//")
        #if os.path.isfile(sample):
            #continue
        csample=sample.split("/")[-1]
        print csample
        listOfSamples.append([os.path.join(outputFolder,csample+".root"),sample,samplelist,options])

    log.info("Now merging files:")
    if len(listOfSamples)>0:
        #now merge all samples
        pool = multiprocessing.Pool(10)
        pool.map_async(hadd, listOfSamples)
        while True:
            time.sleep(5)
            if not pool._cache: break
    else:
        log.error("Nothing to do?")
        exit()
    hasData=False
    dataSamples=[]
    for data in glob.glob(outputFolder+"/"+"*Run*.root"):
        if not "RunII" in data:
            hasData=True
            data=data.replace(".root","")
            if data not in dataSamples:
                dataSamples.append(data)
    for dataSample in dataSamples:
        shutil.move(outputFolder+"/"+"%s.root"%(os.path.basename(dataSample)),outputFolder+"/"+"Data_%s.root"%(os.path.basename(dataSample)))
    if hasData:
        all_merged_data_files=[outputFolder+"/"+"Data_%s.root "%(os.path.basename(dataSample))  for dataSample in dataSamples]
        command= "hadd %s "%options.hadd+outputFolder+"/"+"allData.root "+" ".join(all_merged_data_files)
        print(command)
        if not options.dryrun:
            p = subprocess.Popen(command,shell=True, stdout = subprocess.PIPE, stderr = subprocess.STDOUT)
            out, err = p.communicate()
            log.debug(out)
            log.debug(err)
    log.info("Done")


#method to add the Files at the end
def hadd(item):
    out, err="",""
    #item=os.path.join(outputFolder,csample+".root"),sample,samplelist,options
    outputname, sample, samplelist, options =item
    overwrite=options.force
    if (not os.path.exists(outputname)) or overwrite:
        if len(samplelist)==1:
            if "root:" in samplelist[0]:
                calling= "xrdcp "+samplelist[0]+" "+outputname
            elif "eos" in samplelist[0]:
                calling= "cp "+samplelist[0]+" "+outputname
            else:
                calling= "mv "+samplelist[0]+" "+outputname
        elif len(samplelist)==0:
            calling='echo "Error no File for %s"'%(sample)
            print(calling)
        else:
            calling="hadd %s "%options.hadd+outputname+" "+" ".join(samplelist)
        #print(calling)
        if not options.dryrun:
            p = subprocess.Popen(calling,shell=True, stdout = subprocess.PIPE, stderr = subprocess.STDOUT )
            out, err = p.communicate()
            if ("Zomie" in out) or ("Error" in out):
                
                print("-------------Problem in-----------------------")
                print(calling)
                print("------------------------------------")
            log.debug(out)
            log.debug(err)
        else:
            print("This is a dry run and it would have called: "+calling)
        print("%s is finished!"%(outputname))
    return [out, err]

def cleanUp(options):
    log.info('Will delete all input files!!!')
    shutil.rmtree(options.output+"/tmp")
    shutil.rmtree(options.outputFolder.replace("root://cmseos.fnal.gov/","/eos/uscms/"))


if __name__ == "__main__":
    main()
