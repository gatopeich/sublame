import html
import os
import re
import sublime
import sublime_plugin
import subprocess
import time

from os.path import realpath, basename, dirname

DELAY_BETWEEN_UPDATES = 10


def run_shell(args):
    try:
        print('running: %s'%args)
        proc = subprocess.Popen(args, shell=True, stdout=subprocess.PIPE, universal_newlines=True)
    except Exception as e:
        print('gatoHacks: Failed to run command %r: %r' % (args, e))
    return proc


class Repo:
    def __init__(self, filename):
        filename = realpath(filename)
        self.dir_file = dirname(filename), basename(filename)
        self.info = run_shell('svn info "%s"'%filename).stdout.read()
        if self.info:
            self.repo = 'svn'
            return
        self.info = run_shell('cd "%s" && git log "%s"|head'%(self.dir_file)).stdout.read()
        if self.info:
            self.repo = 'git'
            return
        self.repo = None
        self.info = '(Not on svn or git)'

    CHANGES = re.compile(r'\n@@ -(\d+),(\d+) \+(\d+),(\d+) @@[^\n]*((\n[^@][^\n]*)*)',re.DOTALL)
    def diff(self):
        if self.repo == 'svn':
            diffs = run_shell('cd "%s" && svn diff "%s"'%self.dir_file).stdout.read()
        else:
            diffs = run_shell('cd "%s" && git diff HEAD "%s"'%self.dir_file).stdout.read()
        return tuple((int(a),int(b),int(c),int(d),text) for a,b,c,d,text,_ in Repo.CHANGES.findall(diffs))

    def bg_blame(self):
        if self.repo == 'svn':
            return run_shell('cd "%s" && svn blame --non-interactive -g -x -bw "%s"'%self.dir_file)
        return run_shell('cd "%s" && git blame "%s"'%self.dir_file)

    def bg_rev(self, rev):
        if self.repo == 'svn':
            return run_shell('cd "%s" && svn log -vc %d ^/'%(self.dir_file[0],rev))
        return run_shell('cd "%s" && git log %s'%(self.dir_file[0],rev))

def minihtml_escape(text):
    # See issue https://github.com/SublimeTextIssues/Core/issues/2100
    return html.escape(text.replace('&','🙴').replace('"','ʺ').replace("'","ʼ")).replace('ʺ','"').replace("ʼ","'")


class GatoViewListener(sublime_plugin.ViewEventListener):
    def __init__(self, view):
        super().__init__(view)
        # print ("GatoViewListener(%s)" % self.view.file_name())
        self.blame = None
        self.diff = None
        self.info = None
        self.stamp = None,None # Metainfo stamp

    def on_hover(self, point, hover_zone, update=False):
        if not update:
            self.view.hide_popup()
        # self.view.settings().set('dired_stop_preview_thread', True)
        if hover_zone != sublime.HOVER_GUTTER:
            return

        file = self.view.file_name()
        line = self.view.rowcol(point)[0]

        def blame_in_progress(): return type(self.blame) is str
        def blame_done(): return type(self.blame) is list

        stamp = (self.view.change_count(), time.clock()//DELAY_BETWEEN_UPDATES)
        if not update and stamp[0] != self.stamp[0]:
            self.info = Repo(file).info
            self.diff = Repo(file).diff()
            if self.info and not blame_in_progress():
                bg_proc = Repo(file).bg_blame()
                self.blame = ''
                def get_blame():
                    # print('get_blame(%r)'%get_blame)
                    if not blame_in_progress():
                        bg_proc.terminate()
                        return
                    try:
                        self.blame = bg_proc.communicate(timeout=1)[0]
                        self.blame = self.blame.splitlines()
                        print('blame returned code %d and %d lines'%(bg_proc.returncode, len(self.blame)))
                        self.on_hover(point, hover_zone, True)
                        self.stamp = stamp
                    except:
                        sublime.set_timeout(get_blame, 999)
                get_blame()

        b_line = ''
        try:
            b_line = self.blame[line]
            if b_line[0] != 'r':
                b_line = b_line.strip()
                cols = b_line.split()
                try:
                    rev = int(cols[0]) # 1st col might be a merge annotation like "G"
                except:
                    rev = int(cols[1]) # Try 2nd column instead,
                    b_line = b_line[2:].strip() # Skip annotation column
                # Search for rev in repo root to find merged changes too (and easily)
                bg_proc = Repo(file).bg_rev(rev)
                def get_log():
                    # print('get_log(%s)'%rev)
                    if not blame_done():
                        bg_proc.terminate()
                    elif bg_proc.poll() == None:
                        sublime.set_timeout(get_log, 333)
                    else:
                        log = bg_proc.stdout.read().strip('-\n \t')
                        # errors = bg_proc.stderr.read().strip()
                        # if errors: log += "\nERRORS: " + errors
                        # print("Got %r for r%s"%(log, rev))
                        self.blame[line] = 'r' + b_line + '\n' + log
                        self.on_hover(point, hover_zone)
                get_log()
        except:
            if blame_in_progress():
                b_line = '(fetching...)'

        text = ''
        if self.diff:
            for orig_line,orig_size,changed_line,size,change in self.diff:
                if changed_line <= line and changed_line+size >= line:
                    # print (line,changed_line,size,repr(change))
                    # TODO: <a> – a callback can be specified via the API to handle clicks on anchor tags
                    change = minihtml_escape(change).replace('\t','    ').replace(' ','\u00A0')
                    change = re.sub(r'^.[^\s]*(\s+)$', r'<b style="border:1px solid orange;border-radius:2">\1</b>', change, flags=re.M)
                    change = re.sub(r'^(-.*)', r'<b style="color:red">\1</b>', change, flags=re.M)
                    change = re.sub(r'^(\+.*)', r'<b style="color:green">\1</b>', change, flags=re.M)
                    text += '<b><i>diff:</i></b><br>%s<br><br>'%(change.strip())
                    break

        if b_line:
            text += '<b><i>blame:</i></b><br>%s<br><br>'%(minihtml_escape(b_line))
        text += '<b><i>info:</i></b>\n'+minihtml_escape(self.info)
        minihtml = '<body>%s:%s<br>%s</body>'%(file, line+1, text.replace('\n','<br>'))
        if update:
            self.view.update_popup(minihtml)
        else:
            width, height = self.view.viewport_extent()
            self.view.show_popup(minihtml, sublime.HIDE_ON_MOUSE_MOVE_AWAY, point, max_width=width, max_height=height)
            # , on_navigate=self.on_navigate, on_hide=self.on_hide)

    # def on_navigate(self, item):
    #     print('on_navigate(%s)'%item)

    # def on_hide(self):
    #     print('on_hide(%s)'%self.view.file_name())
