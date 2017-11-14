#!/usr/bin/env python

import csv
import os
import optparse
import sys
import re
import time
import datetime
import curses
import subprocess
import condor_submit
import getpass
import multiprocessing
import multiprocessing.pool
import curseshelpers
import pprint
import logging
import collections
import signal
import ROOT
import math
import glob

waitingForExit = False


class NoDaemonProcess(multiprocessing.Process):
    # make 'daemon' attribute always return False
    def _get_daemon(self):
        return False
    def _set_daemon(self, value):
        pass
    daemon = property(_get_daemon, _set_daemon)
class NoDaemonPool(multiprocessing.pool.Pool):
    Process = NoDaemonProcess

def terminate(signum, frame):
    global waitingForExit
    waitingForExit = True

def addtime(tfor,tsince,tto):
    """Add two time intervals
    tfor to tto-tsince
    """
    if tsince==None:
        return tfor
    deltat=tto-tsince
    if tfor==None:
        tfor=deltat
    else:
        tfor=tfor+deltat
    return tfor
def timerepr(deltat):
    """Return formatted time interval
    """
    if deltat.days<0:
        return "now"
    hours, seconds=divmod(deltat.seconds, 60*60)
    minutes, seconds=divmod(seconds, 60)
    if deltat.days: return "in {0}d {1}h {2}m {3}s".format(deltat.days, hours, minutes, seconds)
    if hours: return "in {0}h {1}m {2}s".format(hours, minutes, seconds)
    if minutes: return "in {0}m {1}s".format(minutes, seconds)
    return "in {0}s".format(seconds)


def nextUpdate(lastUpdate, updateInterval, nextTaskId):
    if nextTaskId==0:
        return lastUpdate+datetime.timedelta(seconds=updateInterval)-datetime.datetime.now()
    else:
        return datetime.timedelta(seconds=-1)

def resubmitByStatus(taskList, resubmitList, status, overview):
    """add jobs with a certain status to the resubmit list
    """
    if overview.level==0:
        myTaskIds, myTaskList=list(range(len(taskList))), taskList
    elif overview.level==1:
        myTaskIds, myTaskList=[overview.currentTask], [taskList[overview.currentTask]]
    else:
        myTaskIds, myTaskList=[], []
    for (t, task) in zip(myTaskIds, myTaskList):
        for (j, job) in zip(list(range(len(task.jobs))), task.jobs):
            if job.status in status:
                if (job.status == "DONE-OK" and job.infos["ExitCode"]!="0") or job.status != "DONE-OK":
                    resubmitList[t].add(j)

def addToList(taskList, myList, overview):
    """add jobs to the kill / resubmit list
    """
    if overview is True:
        # add all tasks
        for t in range(len(taskList)):
            for j in range(len(taskList[t].jobs)):
                myList[t].add(j)
    elif overview.level==0:
        # add one task
        for j in range(len(taskList[overview.currentTask].jobs)):
            myList[overview.currentTask].add(j)
    elif overview.level==1:
        # add one job
        myList[overview.currentTask].add(overview.currentJob)

def clearFinishedJobs(taskList):
    """remove done jobs from the list
    """
    removeList=[]
    for t in taskList:
        if t.frontendstatus == "RETRIEVED":
            removeList.append(t)
    for t in removeList:
        taskList.remove(t)


def statistics(taskList):
    """Draw statistics
    Draws a histogram of the time consumed by the finished jobs
    """
    import rootplotlib
    rootplotlib.init()
    c1=ROOT.TCanvas("c1","",600,600)
    c1.UseCurrentStyle()
    now=datetime.datetime.now()
    totaltimes, runtimes, finished=[],[],[]
    for t in taskList:
        for j in t.jobs:
            try:
                starttime = j.starttime
            except (IndexError, KeyError, AttributeError):
                continue
            try:
                endtime =  j.endtime
            except (IndexError, KeyError, AttributeError):
                endtime=time.time()
            try:
                runtime = j.endtime-j.starttime
            except (IndexError, KeyError, AttributeError):
                runtime=now-j.starttime
            totaltimes.append((endtime-starttime)/60)
            runtimes.append((endtime-runtime)/60)
            if "DONE" in j.status:
                finished.append((endtime-runtime)/60)
    try:
        lo = min(min(totaltimes),min(runtimes))
        hi = max(max(totaltimes),max(runtimes))
    except ValueError:
        return None
    bins=int(min(100,math.ceil(len(runtimes)/10)))
    h1=ROOT.TH1F("h1","",bins,lo,hi)
    h2=ROOT.TH1F("h2","",bins,lo,hi)
    h3=ROOT.TH1F("h3","",bins,lo,hi)
    h1.GetXaxis().SetTitle("#Delta t (min)")
    h1.GetYaxis().SetTitle("Number of jobs")
    h1.SetLineWidth(3)
    h2.SetLineWidth(3)
    h2.SetLineColor(ROOT.kRed)
    h3.SetLineColor(ROOT.kGreen)
    for t in runtimes: h1.Fill(t)
    for t in totaltimes: h2.Fill(t)
    for t in finished: h3.Fill(t)
    h1.Draw("hist")
    h2.Draw("hist same")
    h3.Draw("hist same")
    legend=rootplotlib.Legend(pad=c1)
    legend.AddEntry(h1,"run times","l")
    legend.AddEntry(h2,"total times","l")
    legend.AddEntry(h3,"finished","l")
    legend.Draw()
    c1.Update()
    return (c1, h1, h2, legend)


class Overview:
    """This class incorporates the 'graphical' overviews of tasks, jobs and jobinfo.
    Tasks and jobs overviews are stored persistantly in order to be aware of the selected task/job.
    Jobinfo is created on the fly.
    """
    def __init__(self, stdscr, tasks, resubmitList, killList, nextTaskId):
        self.level = 0
        self.taskId = 0
        self.cursor = 0
        self.stdscr = stdscr
        self.taskOverviews = []
        self.jobstati=""
        self.height=stdscr.getmaxyx()[0]-16
        self.overview = curseshelpers.SelectTable(stdscr, top=10, height=self.height, maxrows=100+len(tasks), footer=True)
        #widths=[2, 100, 12, 12, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9]
        #self.overview.setColHeaders(["", "Task", "Status", "Performance", "Total", "Prep.", "Run.", "RRun.", "Abrt.", "Fail.", "OK", "Good", "None", "R. Time"], widths)
        widths=[2, 100, 12, 12, 9, 9, 9, 9, 9, 9, 9, 9]
        self.overview.setColHeaders(["", "Task", "Status", "Performance", "Total", "Prep.", "Run.", "Fail.", "OK", "Good", "None", "R. Time"], widths)
        for task in tasks:
            taskOverview = curseshelpers.SelectTable(stdscr, top=10, height=self.height, maxrows=100+len(task.jobs))
            widths=[100, 16, 22, 22, 16, 10]
            taskOverview.setColHeaders(["Job", "Status", "In Status since", "Running for", "FE-Status", "Exit Code"], widths)
            self.taskOverviews.append(taskOverview)
        self.update(tasks, resubmitList, killList, nextTaskId)
        self.tasks = tasks
    def update(self, tasks, resubmitList, killList, nextTaskId):
        if (nextTaskId) % len(tasks) ==0:
            command="condor_q %s"%(getpass.getuser())
            proc = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
            self.jobstati = proc.stdout.read()
            self.tasks = tasks
            for task in self.tasks:
                task.status(self.jobstati)
        else:
            return
        #parameters = [tasks[nextTaskId], resubmitList[nextTaskId], killList[nextTaskId]]
        #killJobs=killList[nextTaskId]
        #resubmitJobs=resubmitList[nextTaskId]
        #if len(killJobs) > 0:
            #task.kill(killJobs)
        #if len(resubmitJobs) > 0:
            #task.resubmit(resubmitJobs)
        
        self.overview.clear()
        totalstatusnumbers = collections.defaultdict(int)
        for (taskId, taskOverview, task) in zip(list(range(len(tasks))), self.taskOverviews, tasks):
            statusnumbers=task.jobStatusNumbers()
            if statusnumbers['good'] + statusnumbers['bad'] == 0:
                performance = None
                strperformance = ''
            else:
                performance = statusnumbers['good']/(statusnumbers['good']+statusnumbers['bad'])
                strperformance = '{0:>6.1%}'.format(performance)
            #formatting
            if performance is None:
                # blue
                printmode = curses.color_pair(4)
            elif performance <=0.95:
                # red
                printmode = curses.color_pair(1)
            elif 0.95<performance<1:
                # yellow
                printmode = curses.color_pair(3)
            else:
                #green
                printmode = curses.color_pair(2)
            if task.frontendstatus != "RETRIEVED":
                printmode = printmode | curses.A_BOLD
            #prepare and add row
            if nextTaskId == taskId:
                icon = ">"
            else:
                icon = " "
            cells = [icon, task.sample, 
                    task.frontendstatus, 
                    strperformance, 
                    statusnumbers['total'], 
                    statusnumbers['PENDING']+ statusnumbers['IDLE']+statusnumbers['SUBMITTED']+statusnumbers['REGISTERED'], 
                    statusnumbers['RUNNING'], 
                    #statusnumbers['REALLY-RUNNING'], 
                    #statusnumbers['ABORTED'], 
                    statusnumbers['ERROR']+statusnumbers['FAILED']+statusnumbers['DONE-FAILED'], 
                    statusnumbers['COMPLETED']+statusnumbers['DONE-OK'], 
                    statusnumbers['good'], 
                    statusnumbers[None]+statusnumbers["None"], 
                    task.run_time()]
            self.overview.addRow(cells, printmode)
            for key in statusnumbers:
                totalstatusnumbers[key]+=statusnumbers[key]
            taskOverview.clear()
            for job in task.jobs:
                try:
                    jobid = job.id
                except AttributeError:
                    jobid = ""
                try:
                    jobstatus = job.status
                except AttributeError:
                    jobstatus = ""
                try:
                    jobfestatus = job.frontendstatus
                except AttributeError:
                    jobfestatus = ""
                try:
                    jobreturncode=job.infos["ExitCode"]
                except (KeyError, AttributeError):
                    jobreturncode = ""
                try:
                    jobsince = job.starttime
                except (KeyError, AttributeError, IndexError):
                    jobsince = ""
                try:
                    if job.endtime is not None:
                        jobrunningfor = job.endtime-job.starttime
                    else:
                        jobrunningfor = datetime.datetime.now()-job.starttime
                except (KeyError, AttributeError):
                    jobrunningfor = ""
                cells = [jobid, jobstatus, jobsince, jobrunningfor, jobfestatus, jobreturncode]
                if job.id in resubmitList[taskId] or job.id in killList[taskId]:
                    printmode = curses.color_pair(5) | curses.A_BOLD
                elif jobstatus in ['FAILED', 'ABORTED']:
                    printmode = curses.color_pair(1) | curses.A_BOLD
                elif jobfestatus == "RETRIEVED":
                    if jobreturncode == "0":
                        printmode=curses.color_pair(2)
                    else:
                        printmode = curses.color_pair(1) | curses.A_BOLD
                elif "RUNNING" in jobstatus:
                    printmode = curses.color_pair(2) | curses.A_BOLD
                else:
                    printmode = curses.A_BOLD
                taskOverview.addRow(cells, printmode)
        if totalstatusnumbers['good'] + totalstatusnumbers['bad'] == 0:
            performance = None
            strperformance = ''
        else:
            performance = totalstatusnumbers['good']/(totalstatusnumbers['good']+totalstatusnumbers['bad'])
            strperformance = '{0:>6.1%}'.format(performance)
        cells = ["", 
                "TOTAL", "", 
                strperformance, 
                totalstatusnumbers['total'], 
                totalstatusnumbers['PENDING']+ totalstatusnumbers['IDLE']+totalstatusnumbers['SUBMITTED']+totalstatusnumbers['REGISTERED'],
                totalstatusnumbers['RUNNING'], 
                #totalstatusnumbers['REALLY-RUNNING'], 
                #totalstatusnumbers['ABORTED'], 
                totalstatusnumbers['FAILED'], 
                totalstatusnumbers['Done OK'], 
                totalstatusnumbers['good'], 
                totalstatusnumbers[None]+totalstatusnumbers["None"], 
                totalstatusnumbers['RETRIEVED']]
        self.overview.setFooters(cells)
        self._refresh()
    @property
    def currentTask(self):
        return self.overview.cursor
    @property
    def currentJob(self):
        return self.taskOverviews[self.currentTask].cursor
    def up(self):
        self.level=max(self.level-1,0)
        self._refresh()
    def down(self):
        self.level=min(self.level+1,2)
        self._refresh()
    def _refresh(self):
        if self.level==0:
            self.currentView = self.overview
            self.currentView.refresh()
        elif self.level==1:
            self.currentView = self.taskOverviews[self.currentTask]
            self.currentView.refresh()
    def level2(self, passphrase):
        pp = pprint.PrettyPrinter(indent=4)
        x = curseshelpers.TabbedText(self.stdscr, top=10, height=self.height-2)
        if self.tasks[self.currentTask].jobs[self.currentJob].frontendstatus=="Error" or self.tasks[self.currentTask].jobs[self.currentJob].frontendstatus=="Done OK" :
            try:
                x.addFile("stdout", self.tasks[self.currentTask].jobs[self.currentJob].outfile)
            except:
                x.addText("stdout","could not find stdout"+ self.tasks[self.currentTask].jobs[self.currentJob].outfile)
            try:
                x.addFile("stderr",self.tasks[self.currentTask].jobs[self.currentJob].errorfile)
            except:
                x.addText("stderr","could not find stderr")
        else:
            x.addText("Status information", "No information available")
        self.currentView=x
        self.currentView.refresh()

def main(stdscr, options, taskList, passphrase):
    # Logging
    #logger = logging.getLogger()
    #logger.setLevel(logging._levelNames[options.debug.upper()])
    #logQueue = multiprocessing.Queue()
    #logText = curseshelpers.BottomText(stdscr, stdscr.getmaxyx()[0]-5)
    #handler = curseshelpers.CursesMultiHandler(stdscr, logText, logQueue, level=logging._levelNames[options.debug.upper()])
    #fileHandler = logging.FileHandler("television.log")
    #formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    #handler.setFormatter(formatter)
    #fileHandler.setFormatter(formatter)
    #logging.addHandler(handler)
    #logger.addHandler(fileHandler)

    # catch sigterm to terminate gracefully
    signal.signal(signal.SIGTERM, terminate)
    # curses color pairs
    curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_BLUE, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
    # create lists
    resubmitList, killList = [], []
    for task in taskList:
        resubmitList.append(set())
        killList.append(set())
    curses.noecho()
    stdscr.keypad(1)
    updateInterval=600
    lastUpdate=datetime.datetime.now()

    # paint top rows
    stdscr.addstr(0, 0, ("{0:^"+str(stdscr.getmaxyx()[1])+"}").format("television"), curses.A_REVERSE)
    stdscr.timeout(1)
    curses.curs_set(0)
    stdscr.refresh()
    # get validity of the certificate
    certtime=datetime.datetime.now()+datetime.timedelta(seconds=condor_submit.timeLeftVomsProxy())
    # waitingForUpdate stores the current task when its updated. waitingForExit is needed to wait for all jobs to finish before exiting
    global waitingForExit
    waitingForUpdate = None
    nextTaskId=0
    overview = Overview(stdscr, taskList, resubmitList, killList, nextTaskId)
    # this is the pool for the update task.
    pool = None
    while True:
        # main loop
        curseshelpers.bicolor(stdscr, 1, 0, "(<.*?>)", curses.color_pair(0), curses.color_pair(3)|curses.A_BOLD, "Exit <q>  Back <BACKSPACE>  Raise/lower update interval <+>/<-> ("+str(updateInterval)+")  More information <return>  Update <space>  Statistics <s> ")
        curseshelpers.bicolor(stdscr, 2, 0, "(<.*?>)", curses.color_pair(0), curses.color_pair(3)|curses.A_BOLD, "Resubmit by Status:  Aborted <1>, Done-Failed <2>, (Really-)Running <3>, None <4>, Done-Ok exit!=0 <5>")
        curseshelpers.bicolor(stdscr, 3, 0, "(<.*?>)", curses.color_pair(0), curses.color_pair(3)|curses.A_BOLD, "Resubmit job/task <r> Resubmit all tasks <R>  Kill job/task <k> Kill all tasks <K> clear finished <cC>")
        curseshelpers.bicolor(stdscr, 4, 0, "(<.*?>)", curses.color_pair(0), curses.color_pair(3)|curses.A_BOLD, "Next update {0}       ".format(timerepr(nextUpdate(lastUpdate, updateInterval, nextTaskId))))
        stdscr.addstr(5, 0, "Certificate expires {0}       ".format(timerepr(certtime-datetime.datetime.now())))
        if waitingForExit:
            stdscr.addstr(6,0,"Exiting... Waiting for status retrieval to finish...", curses.color_pair(1) | curses.A_BOLD)
        stdscr.refresh()
        # refresh overview (the task/job table or the jobinfo text)
        overview.currentView.refresh()
        #logText.refresh()
        if nextUpdate(lastUpdate, updateInterval, nextTaskId).days<0 or waitingForUpdate is not None:
            # should an update be performed or is ongoing?
            if waitingForUpdate is not None:
                # update ongoing
                overview.update(taskList, resubmitList, killList, nextTaskId)
                lastUpdate = datetime.datetime.now()
                waitingForUpdate = None
            else:
                # no update ongoing, then start a new task to update
                if passphrase:
                    condor_submit.checkAndRenewVomsProxy(648000, passphrase=passphrase)
                    certtime=datetime.timedelta(seconds=condor_submit.timeLeftVomsProxy())+datetime.datetime.now()
                # prepare parameters
                parameters = [taskList[nextTaskId], resubmitList[nextTaskId], killList[nextTaskId]]
                # use one process only, actual multiprocessing is handled within this process (multiple jobs per tasks are retrieved)

                overview.update(taskList, resubmitList, killList, nextTaskId)
                # reset resubmit list for this task
                resubmitList[nextTaskId], killList[nextTaskId] = set(), set()
                waitingForUpdate = nextTaskId
                nextTaskId = (nextTaskId+1) % len(taskList)

        # user key press processing
        c = stdscr.getch()
        if c == ord('+'):
            updateInterval+=60
        elif c == ord('-'):
            updateInterval=max(0,updateInterval-60)
        elif c == curses.KEY_BACKSPACE:
            if overview.level:
                overview.up()
        elif c == ord('q'):
            waitingForExit=True
        elif c == ord(' '):
            lastUpdate = datetime.datetime.now()-datetime.timedelta(seconds=2*updateInterval)
        elif c == curses.KEY_LEFT:
            overview.currentView.goLeft()
        elif c == curses.KEY_RIGHT:
            overview.currentView.goRight()
        elif c == curses.KEY_DOWN:
            overview.currentView.goDown()
        elif c == curses.KEY_UP:
            overview.currentView.goUp()
        elif c == curses.KEY_NPAGE:
            overview.currentView.pageDown()
        elif c == curses.KEY_PPAGE:
            overview.currentView.pageUp()
        elif c == curses.KEY_HOME:
            overview.currentView.home()
        elif c == curses.KEY_END:
            overview.currentView.end()
        elif c == ord('1'):
            resubmitByStatus(taskList, resubmitList, ["ABORTED"], overview)
            overview.update(taskList, resubmitList, killList, nextTaskId)
        elif c == ord('2'):
            resubmitByStatus(taskList, resubmitList, ["FAILED"], overview)
            overview.update(taskList, resubmitList, killList, nextTaskId)
        elif c == ord('3'):
            resubmitByStatus(taskList, resubmitList, ["RUNNING", "REALLY-RUNNING"], overview)
            overview.update(taskList, resubmitList, killList, nextTaskId)
        elif c == ord('4'):
            resubmitByStatus(taskList, resubmitList, ["None", None], overview)
            overview.update(taskList, resubmitList, killList, nextTaskId)
        elif c == ord('5'):
            resubmitByStatus(taskList, resubmitList, ["Done OK"], overview)
            overview.update(taskList, resubmitList, killList, nextTaskId)
        elif c==ord('r'):
            addToList(taskList, resubmitList, overview)
            overview.update(taskList, resubmitList, killList, nextTaskId)
        elif c==ord('R'):
            addToList(taskList, resubmitList, True)
            overview.update(taskList, resubmitList, killList, nextTaskId)
        elif c==ord('k'):
            addToList(taskList, killList, overview)
            overview.update(taskList, resubmitList, killList, nextTaskId)
        elif c==ord('K'):
            addToList(taskList, killList, True)
            overview.update(taskList, resubmitList, killList, nextTaskId)
        elif c==ord('c'):
            clearFinishedJobs(taskList)
            overview = Overview(stdscr, taskList, resubmitList, killList, 0)
        elif c==ord('C'):
            clearFinishedJobs(taskList)
            overview = Overview(stdscr, taskList, resubmitList, killList, 0)
        elif c==ord('s'):
            dummy = statistics(taskList)
        elif c==ord('t'):
            logging.warning("warning")
        elif c == 10:   #enter key
            overview.down()
            if overview.level==2:
                overview.level2(passphrase)
        else:
            pass
        if waitingForExit and waitingForUpdate is None:
            break
    curses.nocbreak(); stdscr.keypad(0); curses.echo()
    curses.endwin()

def getTasks(sample_list,run_folder):
    taskList=[]
    for sample in sample_list:
        try:
            task = condor_submit.Task(sample,run_folder)
        except:
            continue
        taskList.append(task)
    return taskList


if __name__ == "__main__":
    logging.basicConfig(format='%(levelname)s:%(message)s',filename='log.log',level=logging.DEBUG)
    parser = optparse.OptionParser( description='Monitor for condor tasks', usage='usage: %prog directories')
    parser.add_option("--debug", action="store", dest="debug", help="Debug level (DEBUG, INFO, WARNING, ERROR, CRITICAL)", default="WARNING")
    parser.add_option("-p", "--proxy", action="store_true", dest="proxy", help="Do not ask for password and use current proxy", default=False)
    (options, args) = parser.parse_args()
    
    if len(args)==0 and not os.path.exists("submitted_samples.txt"):
        logging.error( "You must give either a input file or keep the submitted_samples.txt" )
        sys.exit(3)
        
    elif (len(args)==0 and os.path.exists("submitted_samples.txt")):
        f=open("submitted_samples.txt","r")
        for line in f:
            if "outFolder:" in line:
                args=[line.replace("outFolder:","").strip()]
                break
            
    if "/eos/uscms/" in args[0]:
        args[0]=args[0].split(str(getpass.getuser())+"/")[-1]
    run_folders=args
    
    sample_list=[]
    for a in args:
        sample_list+=glob.glob(a+"/*")
    sample_list=[i.split("/")[-1] for i in sample_list]
    sample_list=filter(lambda x: ("exe" not in x),sample_list)
    
    taskList=[]
    for folder in run_folders:
        taskList += getTasks(sample_list,folder)
    

    if options.proxy:
        passphrase=None
    else:
        print("You may enter your grid password here. Do not enter anything to use the available proxy.")
        passphrase = getpass.getpass()
        if passphrase=="":
            passphrase = None
        else:
            condor_submit.renewVomsProxy(passphrase=passphrase)
    curses.wrapper(main, options, taskList, passphrase)

