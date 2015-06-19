#!/usr/bin/env python
"""Functions that constitute the :obj:`argdoc` extension for `Sphinx`_.

User functions
--------------
:func:`noargdoc`
    Function decorator that forces :obj:`argdoc` to skip a :term:`main-like function`
    it would normally process
    
Developer functions
-------------------

:func:`format_argparser_to_docstring`
    Extract tables of arguments from an :class:`~argparse.ArgumentParser`
    and from all of its subprograms, and format their descriptions &
    help text.

:func:`get_subcommand_tables`
    Extract tables from all subcommands
    :class:`ArgumentParsers <argparse.ArgumentParser>`
    contained by an enclosing :class:`~argparse.ArgumentParser`

:func:`post_process_automodule`
    Event handler to activate argdoc upon `autodoc-process-docstring` events

:func:`setup`
    Register the extension with the running `Sphinx`_ instance
"""
import sys
import re
import shlex
import subprocess
import os
import codecs

import sphinx
import argdoc
from sphinx.errors import ConfigError

#===============================================================================
# INDEX: various constants
#===============================================================================

_OTHER_HEADER_LINES = u"""Script contents
---------------""".split("\n")

_REQUIRED = [
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
]
"""Other `Sphinx`_ extensions required by :py:obj:`argdoc`"""



_HEADERS = "=-~._\"'^;"
_INDENT_SIZE = 4
_SEPARATOR = "\n------------\n\n".split("\n")

#===============================================================================
# INDEX: helper functions 
#===============================================================================

def get_patterns(prefix_chars="-"):
    """Retrieve a dictionary of regular expressions that separate argument names
    from their values and descriptions
    
    Parameters
    ----------
    prefix_chars : str, optional
        String of prefix characters that the :class:`~argparse.ArgumentParser`
        uses (Default: `'-'`)
    
    Returns
    -------
    dict
        Dictionary of regular expression patterns
    """
    patterns = { "section_title"      : r"^(\w+.*):$",
                 "positional_arg"     : r"^  (?P<arg1>[^{}\s-]+)(?:\s\s+(?P<desc>\w+.*))?$",
                 "arg_only"           : r"^  (?P<arg1>-?[^\s,]+)(?:, (?P<arg2>--[^\s]+))?$",
                 "arg_plus_val"       : r"^  (?P<arg1>-+[^\s]+)(?P<val1>(?: [^-\s]+)+)(?:(?:, (?P<arg2>--[^\s]+))(?P<val2>(?: [^\s]+)+))?$",
                 "arg_plus_desc"      : r"^  (?P<arg1>-?[^\s]+)(?:,\s(?P<arg2>--[^\s]+))?\s\s+(?P<desc>.*)",
                 "arg_plus_val_desc"  : r"^  (?P<arg1>-+[^\s]+)(?P<val1>(?: [^-\s]+)+)(?:(?:, (?P<arg2>--[^\s]+))(?P<val2>(?: [^\s]+)+))?  +(?P<desc>\w+.*)$",
                 "continue_desc"      : r"^ {24}(.*)",
                 "section_desc"       : r"^  ((?:[^-\s][^\s]*)(?:\s[^\s]+)+)$",
                 "subcommand_names"   : r"^  {((?:\w+)(?:(?:,(?:\w+))+)?)}$"             
                }
    """Regular expressions describing components of docstrings created by :py:mod:`argparse`"""
    
    patterns = { K : re.compile(V) for K,V in patterns.items() }
    return patterns  

def get_col1_text(matchdict):
    """Format argument name(s) and value(s) for column 1 of argument tables

    Parameters
    ----------
    matchdict : dict
        Dictionary of values

    Returns
    -------
    str (unicode if Python 2.7)
    """
    if "val1" in matchdict:
        tmpstr = "``%s %s``" % (matchdict["arg1"],matchdict["val1"])
        if matchdict.get("arg2") is not None:
            tmpstr += (", ``%s %s``" % (matchdict["arg2"],matchdict["val2"]))
    else:
        tmpstr = "``%s``" % matchdict["arg1"]
        if matchdict.get("arg2") is not None:
            tmpstr += (", ``%s``" % matchdict["arg2"])

    if sys.version_info[0] == 2 and isinstance(tmpstr,str):
        tmpstr = unicode(tmpstr,"utf-8")
        
    return tmpstr

def get_col2_text(matchdict):
    """Format argument descriptions, if present, for column 2 of argument tables

    Parameters
    ----------
    matchdict : dict
        Dictionary of values

    Returns
    -------
    str (unicode if Python 2.7)
    """
    tmpstr =  matchdict.get("desc","") if matchdict.get("desc") is not None else ""
    if sys.version_info[0] == 2 and isinstance(tmpstr,str):
        tmpstr = unicode(tmpstr,"utf-8")
        
    return tmpstr

#===============================================================================
# INDEX: function decorators
#===============================================================================

def noargdoc(func):
    """Decorator that forces argdoc to skip processing of `func` 
    
    Parameters
    ----------
    func : function
        :term:`main-like function` of a script

    
    Returns
    -------
    func
        wrapped function
    """
    func.__dict__["noargdoc"] = True
    return func

#===============================================================================
# INDEX: docstring-processing functions
#===============================================================================

def get_subcommand_tables(app,obj,help_lines,patterns,start_line,section_head=True,pre_args=0,header_level=1):
    """Processes help output from an :py:class:`argparse.ArgumentParser`
    from a program that includes one or more subprograms.  Called by
    :func:`process_argparser`
    
    Parameters
    ----------
    app
        Sphinx application instance
    
    obj : object
        Object (e.g. module, class, function) to document
            
    help_lines : list
        List of strings, each corresponding to a line of output from having
        passed ``--help`` as an argument to the :term:`main-like function`

    
    patterns : dict
        Dictionary names of line types in argparse output to regular expression
        patterns that process those line types

    start_line : int
        Line in argparse help output containing subcommand header

    section_head : bool, optional
        If `True`, a section header for "Command-line arguments" will be included
        in the output. (Default: `True`)

    pre_args : int, optional
        Number of arguments required to be supplied before subcommand help
        can be retrieved (Default: `0`)
        
    header_level : int, optional
        Level of header to use for `section_name`. Lower numbers are higher
        precedence. (Default: `1`)        
    
    Returns
    -------
    list
        List of strings encoding reStructuredText table of command-line
        arguments for all subprograms in the containing argparser
    """
    out_lines = []
    for line in help_lines[start_line:]:
        match = patterns["subcommand_names"].search(line.strip("\n")) 
        if match is not None:
            subcommands = match.groups()[0].split(",")
            break
    
    app.debug("%s subcommands: %s" % (obj.__name__,", ".join(subcommands)))
    prearg_text = " ".join(["X"]*pre_args)
    for subcommand in subcommands:
        app.debug("Testing subcommand %s with %s preargs" % (subcommand,pre_args))
        call = shlex.split("python -m %s %s %s --help" % (obj.__name__,prearg_text,subcommand))
        try:
            proc = subprocess.Popen(call,stdout=subprocess.PIPE,universal_newlines=True)
            sub_help_lines = proc.communicate()[0].split("\n")

            out_lines.extend(format_argparser_to_docstring(app,
                                                           obj,
                                                           sub_help_lines,
                                                           patterns,
                                                           section_head=section_head,
                                                           header_level=header_level+1,
                                                           section_name=u"``%s`` subcommand arguments" % subcommand,
                                                           _is_subcommand=True)) 
        except subprocess.CalledProcessError as e:
            out  = ("-"*75) + "\n" + e.output + "\n" + ("-"*75)
            out += "Could not call module %s as '%s'. Output:\n"% (obj.__name__, e.cmd)
            out += e.output
            out += ("-"*75) + "\n"
            app.warn(out)

    return out_lines

def format_argparser_to_docstring(app,obj,help_lines,patterns,
                                  section_head=True,section_name=u"Command-line arguments",
                                  header_level=1,
                                  _is_subcommand=False
                                  ):
    """Processes help output from an :py:class:`argparse.ArgumentParser`
    of subprograms, or of a program that has no subprograms. Called by
    :func:`process_argparser`
    
    Parameters
    ----------
    app
        Sphinx application instance
    
    obj : object
        Object (e.g. module, class, function) to document
            
    help_lines : list
        List of strings, each corresponding to a line of output from having
        passed ``--help`` as an argument to the :term:`main-like function`

    patterns : dict
        Dictionary names of line types in argparse output to regular expression
        patterns that process those line types
    
    section_head : bool, optional
        If `True`, a section header for "Command-line arguments" will be included.
        This messes up parsing for function docstrings, but is fine for module
        docstrings (Default: `False`).
    
    section_name : str, optional
        A name or title for the current program or subcommand.
        (Default: `'Command-line arguments'`)
    
    header_level : int, optional
        Level of header to use for `section_name`. Lower numbers are higher
        precedence. (Default: `1`)
    
    _is_subcommand : bool, optional
        If `True`, include module docstring in output. Required for subcommands
        whose help won't be included by in the module docstring found by 
        autodoc. (Default: `False`) 
        
    
    Returns
    -------
    list
        List of strings encoding reStructuredText table of arguments
        for program or subprogram
    """
    started = False

    out_lines = []  # lines we will output
    positional_args = 0
    # the following are wiped & re-initialized for each section
    col1      = []  # holder for column 1 contents: argument names
    col2      = []  # holder for column 2 contents: argument descriptions
    section_title = [] # title of current section
    section_desc  = [] # description of current section
    
    # markers for beginning and end of subcommand docstring descriptions
    desc_start = None
    desc_end   = None
    
    for n,line in enumerate(help_lines):
        line = line.rstrip()
        if _is_subcommand == True and desc_start is None:
            if line.strip() == "":
                desc_start = n+1

        if len(line.strip()) == 0 and started == True and len(col1) > 0 and len(col2) > 0:
            # if current argument group is finished, format table of arguments for export
            # and append it to `out_lines`
            col1_width = 1 + max([len(X) for X in col1])
            col2_width = max([len(X) for X in col2])
            table_header = (u" "*(_INDENT_SIZE))+(u"="*col1_width) + u" " + (u"="*col2_width)
            out_lines.append(u"")
            out_lines.extend(section_title)
            out_lines.extend(section_desc)
            out_lines.append(u"")
            out_lines.append(table_header)
            out_lines.append( (u" "*(_INDENT_SIZE))+u"*Option*" + u" "*(1 + col1_width - 8) + u"*Description*")
            out_lines.append(table_header.replace("=","-"))
             
            for c1, c2 in zip(col1,col2):
                out_lines.append((u" "*(_INDENT_SIZE))+ c1 + (u" "*(1+col1_width-len(c1))) + c2)
 
            out_lines.append(table_header)
            out_lines.append(u"")
            
            # reset section-specific variables
            section_title = []
            section_desc  = []
            col1 = []
            col2 = []
            
        #elif patterns["section_title"].search(line) is not None and not line.endswith("usage:"):
        #FIXME: this is a kludge to deal with __doc__ lines that have trailing colons
        #       and will not work if the first argument section is not one of the following
        #       "positional arguments:" or "optional arguments:"
        elif line.endswith("arguments:"):
            # Found first argument section. Create command-line argument heading
            if started == False:
                started = True
                desc_end = n
                if section_head == True:
                    stmp1 = section_name
                    stmp2 = _HEADERS[header_level]*len(section_name)
                    out_lines.extend(_SEPARATOR)
                    out_lines.append(stmp1)
                    out_lines.append(stmp2)
                    # if is a subcommand, put cached description under heading
                    if _is_subcommand == True:
                        out_lines.extend(help_lines[desc_start:desc_end])
            
            # Create paragraph header for the argument section
            match = patterns["section_title"].match(line)
            section_title = [match.groups()[0].capitalize(),
                             _HEADERS[header_level+1]*len(match.groups()[0]),
                            ]
        elif patterns["section_title"].match(line) is not None and not line.startswith("usage:"):
            # Found section section of arguments.
            # Create paragraph header
            app.debug("Found section title: '%s'" % line)
            match = patterns["section_title"].match(line)
            section_title = [match.groups()[0].capitalize(),
                             _HEADERS[header_level+1]*len(match.groups()[0]),
                            ]
        elif patterns["section_desc"].match(line) is not None:
            section_desc.append(line)
                        
        elif started == True:
            matchdict = None
            match = None
            for pat in ["positional_arg",
                        "arg_only",
                        "arg_plus_val",
                        "continue_desc",
                        "arg_plus_desc",
                        "arg_plus_val_desc",
                        "subcommand_names"
                        ]:
                match = patterns[pat].match(line)
                if match is not None:
                    if pat == "continue_desc":
                        col2[-1] += line.strip("\n")
                        break
                    elif pat == "positional_arg":
                        matchdict = match.groupdict()
                        col1.append(get_col1_text(matchdict))
                        col2.append(get_col2_text(matchdict))
                        positional_args += 1
                        break
                    elif pat == "subcommand_names":
                        new_lines = get_subcommand_tables(app,
                                                          obj,
                                                          help_lines,
                                                          patterns,
                                                          n,
                                                          section_head=section_head,
                                                          header_level=header_level+2,
                                                          pre_args=positional_args,
                                                         )
                        out_lines.extend(new_lines)
                        break
                    else:
                        matchdict = match.groupdict()
                        col1.append(get_col1_text(matchdict))
                        col2.append(get_col2_text(matchdict))
                        break
                    
    
    return out_lines

def post_process_automodule(app,what,name,obj,options,lines):
    """Insert a table listing and describing an executable script's command-line
    arguments into its ``:automodule:`` documentation.
    
    Any :term:`main-like function` decorated with the :func:`noargdoc` decorator
    will be skipped. A function is determined to be a :term:`main-like function`
    if its name matches the name set in the configuration option
    ``argdoc_main_func`` inside ``conf.py``. The default value for
    ``argdoc_main_func`` is `main`.
    
    
    Notes
    -----
    Per the `autodoc`_ spec, this function modifies `lines` in place.
    
    
    Parameters
    ----------
    app
        Sphinx application instance
    
    what : str
        Type of object (e.g. "module", "function", "class")
    
    name : str
        Fully-qualified name of object
    
    obj : object
        Object (e.g. module, class, function) to document
    
    options : object
        Options given to the directive, whose boolean properties are set to `True`
        if their corresponding flag was given in the directive

    lines : list
        List of strings encoding the module docstrings after `Sphinx`_ processing

    Raises
    ------
    :class:`~sphinx.errors.ConfigError`
       If `argdoc_main_func` is defined in ``conf.py`` and is not a `str`
    """
    funcname = app.config.argdoc_main_func
    patterns = get_patterns("-")
    if not isinstance(funcname,str):
        message = "Incorrect type for `argdoc_main_func. Expected `str`, found, `%s` with value `%s`)" % (type(funcname),funcname)
        raise ConfigError(message)

    if what == "module" and obj.__dict__.get(funcname,None) is not None:
        if obj.__dict__.get(funcname).__dict__.get("noargdoc",False) == False:
            call = shlex.split("python -m %s --help" % obj.__name__)
            try:
                proc = subprocess.Popen(call,stdout=subprocess.PIPE,universal_newlines=True)
                help_lines = proc.communicate()[0].split("\n")
            except subprocess.CalledProcessError as e:
                out  = ("-"*75) + "\n" + e.output + "\n" + ("-"*75)
                out += "Could not call module %s as '%s'. Output:\n"% (obj.__name__, e.cmd)
                out += e.output
                out += ("-"*75) + "\n"
                app.warn(out)
            try:
                out_lines = format_argparser_to_docstring(app,obj,help_lines,section_head=True,header_level=1,patterns=patterns)
                out_lines += _SEPARATOR
                lines.extend(out_lines)
                lines.extend(_OTHER_HEADER_LINES)
            except IndexError as e:
                app.warn("Error processing argparser into docstring for module %s: " % obj.__name__)

        if app.config.argdoc_save_rst == True:
            filename = os.path.join(app.outdir,"%s_docstring.rst" % name)
            with codecs.open(filename,encoding="utf-8",mode="wb") as fout:
                for n,line in enumerate(lines):
                    try:
                        if sys.version_info[0] == "2":
                            if isinstance(line,str):
                                line = line.encode("utf-8")
                        
                        fout.write(line)
                        fout.write(u"\n")
                    except Exception as e:
                        app.warn("Could not write out line %s of file %s." % (n,name))
                        raise e
    
            fout.close()
                
        app.emit("argdoc-process-docstring",what,name,obj,options,lines)


#===============================================================================
# INDEX: extension setup
#===============================================================================

def setup(app):
    """Set up :obj:`argdoc` extension and register with `Sphinx`_
    
    Parameters
    ----------
    app
        Sphinx application instance
    """

    metadata = { "version" : argdoc.__version__
               }

    for ext in _REQUIRED:
        app.setup_extension(ext)
    
    app.connect("autodoc-process-docstring",post_process_automodule)
    app.add_config_value("argdoc_main_func","main","env")
    app.add_config_value("argdoc_save_rst",False,"env")
#    app.add_config_value("argdoc_arg_prefix_char","-","env")

    app.add_event("argdoc-process-docstring")

    if sphinx.version_info >= (1,3,):
        return metadata
