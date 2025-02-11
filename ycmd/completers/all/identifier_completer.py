# Copyright (C) 2011-2020 ycmd contributors
#
# This file is part of ycmd.
#
# ycmd is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# ycmd is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with ycmd.  If not, see <http://www.gnu.org/licenses/>.

import os
from collections import defaultdict
from ycmd.completers.general_completer import GeneralCompleter
from ycmd import identifier_utils
from ycmd.utils import ImportCore, LOGGER, SplitLines
from ycmd import responses
ycm_core = ImportCore()

SYNTAX_FILENAME = 'YCM_PLACEHOLDER_FOR_SYNTAX'


class IdentifierCompleter( GeneralCompleter ):
  def __init__( self, user_options ):
    super().__init__( user_options )
    self._completer = ycm_core.IdentifierCompleter()
    self._tags_file_last_mtime = defaultdict( int )
    self._max_candidates = user_options[ 'max_num_identifier_candidates' ]


  def ShouldUseNow( self, request_data ):
    return self.QueryLengthAboveMinThreshold( request_data )


  def ComputeCandidates( self, request_data ):
    if not self.ShouldUseNow( request_data ):
      return []

    completions = self._completer.CandidatesForQueryAndType(
      _SanitizeQuery( request_data[ 'query' ] ),
      request_data[ 'first_filetype' ],
      self._max_candidates )

    completions = _RemoveSmallCandidates(
      completions, self.user_options[ 'min_num_identifier_candidate_chars' ] )

    def ConvertCompletionData( i, x ):
      return responses.BuildCompletionData(
              insertion_text = x,
              extra_menu_info='[ID]' + ('' if i < 2 else f' {i}') )

    return [ ConvertCompletionData( i, x ) for i, x in enumerate(completions, 1) ]


  def _AddIdentifier( self, identifier, request_data ):
    filetype = request_data[ 'first_filetype' ]
    filepath = request_data[ 'filepath' ]

    if not filetype or not filepath or not identifier:
      return

    LOGGER.info( 'Adding ONE buffer identifier for file: %s', filepath )
    self._completer.AddSingleIdentifierToDatabase( identifier,
                                                  filetype,
                                                  filepath )


  def _AddPreviousIdentifier( self, request_data ):
    self._AddIdentifier(
      _PreviousIdentifier(
        self.user_options[ 'min_num_of_chars_for_completion' ],
        self.user_options[ 'collect_identifiers_from_comments_and_strings' ],
        request_data ),
      request_data )


  def _AddIdentifierUnderCursor( self, request_data ):
    self._AddIdentifier(
      _GetCursorIdentifier(
        self.user_options[ 'collect_identifiers_from_comments_and_strings' ],
        request_data ),
      request_data )


  def _AddBufferIdentifiers( self, request_data ):
    filetype = request_data[ 'first_filetype' ]
    filepath = request_data[ 'filepath' ]

    if not filetype or not filepath:
      return

    collect_from_comments_and_strings = bool( self.user_options[
      'collect_identifiers_from_comments_and_strings' ] )
    text = request_data[ 'file_data' ][ filepath ][ 'contents' ]
    LOGGER.info( 'Adding buffer identifiers for file: %s', filepath )
    self._completer.ClearForFileAndAddIdentifiersToDatabase(
        _IdentifiersFromBuffer( text,
                                filetype,
                                collect_from_comments_and_strings ),
        filetype,
        filepath )


  def _FilterUnchangedTagFiles( self, tag_files ):
    for tag_file in tag_files:
      try:
        current_mtime = os.path.getmtime( tag_file )
      except Exception:
        LOGGER.exception( 'Error while getting %s last modification time',
                          tag_file )
        continue
      last_mtime = self._tags_file_last_mtime[ tag_file ]

      # We don't want to repeatedly process the same file over and over; we only
      # process if it's changed since the last time we looked at it
      if current_mtime <= last_mtime:
        continue

      self._tags_file_last_mtime[ tag_file ] = current_mtime
      yield tag_file


  def _AddIdentifiersFromTagFiles( self, tag_files ):
    self._completer.AddIdentifiersToDatabaseFromTagFiles(
      ycm_core.StringVector(
        self._FilterUnchangedTagFiles( tag_files ) ) )


  def _AddIdentifiersFromSyntax( self, keyword_list, filetype ):
    filepath = SYNTAX_FILENAME + filetype
    self._completer.ClearForFileAndAddIdentifiersToDatabase(
      ycm_core.StringVector( keyword_list ),
      filetype,
      filepath )


  def OnFileReadyToParse( self, request_data ):
    self._AddBufferIdentifiers( request_data )
    if 'tag_files' in request_data:
      self._AddIdentifiersFromTagFiles( request_data[ 'tag_files' ] )
    if 'syntax_keywords' in request_data:
      self._AddIdentifiersFromSyntax( request_data[ 'syntax_keywords' ],
                                     request_data[ 'first_filetype' ] )


  def OnInsertLeave( self, request_data ):
    self._AddIdentifierUnderCursor( request_data )


  def OnCurrentIdentifierFinished( self, request_data ):
    self._AddPreviousIdentifier( request_data )


# This looks for the previous identifier and returns it; this might mean looking
# at last identifier on the previous line if a new line has just been created.
def _PreviousIdentifier( min_num_candidate_size_chars,
                         collect_from_comments_and_strings,
                         request_data ):
  def PreviousIdentifierOnLine( line, column, filetype ):
    nearest_ident = ''
    for match in identifier_utils.IdentifierRegexForFiletype(
        filetype ).finditer( line ):
      if match.end() <= column:
        nearest_ident = match.group()
    return nearest_ident

  line_num = request_data[ 'line_num' ] - 1
  column_num = request_data[ 'column_codepoint' ] - 1
  filepath = request_data[ 'filepath' ]
  filetype = request_data[ 'first_filetype' ]

  contents = request_data[ 'file_data' ][ filepath ][ 'contents' ]
  if not collect_from_comments_and_strings:
    contents = identifier_utils.RemoveIdentifierFreeText( contents, filetype )
  contents_per_line = SplitLines( contents )

  ident = PreviousIdentifierOnLine( contents_per_line[ line_num ],
                                    column_num,
                                    filetype )
  if ident:
    if len( ident ) < min_num_candidate_size_chars:
      return ''
    return ident

  line_num = line_num - 1

  if line_num < 0:
    return ''

  prev_line = contents_per_line[ line_num ]
  ident = PreviousIdentifierOnLine( prev_line, len( prev_line ), filetype )
  if len( ident ) < min_num_candidate_size_chars:
    return ''
  return ident


def _RemoveSmallCandidates( candidates, min_num_candidate_size_chars ):
  if min_num_candidate_size_chars == 0:
    return candidates

  return [ x for x in candidates if len( x ) >= min_num_candidate_size_chars ]


def _GetCursorIdentifier( collect_from_comments_and_strings,
                          request_data ):
  filepath = request_data[ 'filepath' ]
  contents = request_data[ 'file_data' ][ filepath ][ 'contents' ]
  filetype = request_data[ 'first_filetype' ]
  if not collect_from_comments_and_strings:
    contents = identifier_utils.RemoveIdentifierFreeText( contents, filetype )
  contents_per_line = SplitLines( contents )
  line = contents_per_line[ request_data[ 'line_num' ] - 1 ]
  return identifier_utils.IdentifierAtIndex(
      line,
      request_data[ 'column_codepoint' ] - 1,
      filetype )


def _IdentifiersFromBuffer( text,
                            filetype,
                            collect_from_comments_and_strings ):
  if not collect_from_comments_and_strings:
    text = identifier_utils.RemoveIdentifierFreeText( text, filetype )
  idents = identifier_utils.ExtractIdentifiersFromText( text, filetype )
  return ycm_core.StringVector( idents )


def _SanitizeQuery( query ):
  return query.strip()
