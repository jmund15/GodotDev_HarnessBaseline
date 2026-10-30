# Godot Class Index — 4.7.1

Generated from the class-reference cache at engine **4.7.1**. 810 classes.

Discovery layer only — brief descriptions, no member lists. For the verbatim signature, parameters, or description of any member, read `.claude/cache/godot-docs/doc/classes/<Class>.xml` (gitignored; populate with `.claude/scripts/godot_docs_cache.sh`). That XML is the source the official HTML class reference is generated from, so it is first-party P1 evidence.

Do not hand-edit — regenerated on every engine bump.

## @GlobalScope
Global scope constants and functions.

## AABB
A 3D axis-aligned bounding box.

## AESContext
Inherits: RefCounted
Provides access to AES encryption/decryption of raw data.

## AStar2D
Inherits: RefCounted
An implementation of A* for finding the shortest path between two vertices on a connected graph in 2D space.

## AStar3D
Inherits: RefCounted
An implementation of A* for finding the shortest path between two vertices on a connected graph in 3D space.

## AStarGrid2D
Inherits: RefCounted
An implementation of A* for finding the shortest path between two points on a partial 2D grid.

## AcceptDialog
Inherits: Window
A base dialog used for user notification.

## AccessibilityServer
Inherits: Object
A server interface for screen reader support.

## AimModifier3D
Inherits: BoneConstraint3D
The [AimModifier3D] rotates a bone to look at a reference bone.

## AnimatableBody2D
Inherits: StaticBody2D
A 2D physics body that can't be moved by external forces. When moved manually, it affects other bodies in its path.

## AnimatableBody3D
Inherits: StaticBody3D
A 3D physics body that can't be moved by external forces. When moved manually, it affects other bodies in its path.

## AnimatedSprite2D
Inherits: Node2D
Sprite node that contains multiple textures as frames to play for animation.

## AnimatedSprite3D
Inherits: SpriteBase3D
2D sprite node in 3D world, that can use multiple 2D textures for animation.

## AnimatedTexture
Inherits: Texture2D
Proxy texture for simple frame-based animations.

## Animation
Inherits: Resource
Holds data that can be used to animate anything in the engine.

## AnimationLibrary
Inherits: Resource
Container for [Animation] resources.

## AnimationMixer
Inherits: Node
Base class for [AnimationPlayer] and [AnimationTree].

## AnimationNode
Inherits: Resource
Base class for [AnimationTree] nodes. Not related to scene nodes.

## AnimationNodeAdd2
Inherits: AnimationNodeSync
Blends two animations additively inside of an [AnimationNodeBlendTree].

## AnimationNodeAdd3
Inherits: AnimationNodeSync
Blends two of three animations additively inside of an [AnimationNodeBlendTree].

## AnimationNodeAnimation
Inherits: AnimationRootNode
An input animation for an [AnimationNodeBlendTree].

## AnimationNodeBlend2
Inherits: AnimationNodeSync
Blends two animations linearly inside of an [AnimationNodeBlendTree].

## AnimationNodeBlend3
Inherits: AnimationNodeSync
Blends two of three animations linearly inside of an [AnimationNodeBlendTree].

## AnimationNodeBlendSpace1D
Inherits: AnimationRootNode
A set of [AnimationRootNode]s placed on a virtual axis, crossfading between the two adjacent ones. Used by [AnimationTree].

## AnimationNodeBlendSpace2D
Inherits: AnimationRootNode
A set of [AnimationRootNode]s placed on 2D coordinates, crossfading between the three adjacent ones. Used by [AnimationTree].

## AnimationNodeBlendTree
Inherits: AnimationRootNode
A sub-tree of many type [AnimationNode]s used for complex animations. Used by [AnimationTree].

## AnimationNodeExtension
Inherits: AnimationNode
Base class for extending [AnimationRootNode]s from GDScript, C#, or C++.

## AnimationNodeOneShot
Inherits: AnimationNodeSync
Plays an animation once in an [AnimationNodeBlendTree].

## AnimationNodeOutput
Inherits: AnimationNode
The animation output node of an [AnimationNodeBlendTree].

## AnimationNodeStateMachine
Inherits: AnimationRootNode
A state machine with multiple [AnimationRootNode]s, used by [AnimationTree].

## AnimationNodeStateMachinePlayback
Inherits: Resource
Provides playback control for an [AnimationNodeStateMachine].

## AnimationNodeStateMachineTransition
Inherits: Resource
A transition within an [AnimationNodeStateMachine] connecting two [AnimationRootNode]s.

## AnimationNodeSub2
Inherits: AnimationNodeSync
Blends two animations subtractively inside of an [AnimationNodeBlendTree].

## AnimationNodeSync
Inherits: AnimationNode
Base class for [AnimationNode]s with multiple input ports that must be synchronized.

## AnimationNodeTimeScale
Inherits: AnimationNode
A time-scaling animation node used in [AnimationTree].

## AnimationNodeTimeSeek
Inherits: AnimationNode
A time-seeking animation node used in [AnimationTree].

## AnimationNodeTransition
Inherits: AnimationNodeSync
A transition within an [AnimationTree] connecting two [AnimationNode]s.

## AnimationPlayer
Inherits: AnimationMixer
A node used for animation playback.

## AnimationRootNode
Inherits: AnimationNode
Base class for [AnimationNode]s that hold one or multiple composite animations. Usually used for .

## AnimationTree
Inherits: AnimationMixer
A node used for advanced animation transitions in an [AnimationPlayer].

## Area2D
Inherits: CollisionObject2D
A region of 2D space that detects other [CollisionObject2D]s entering or exiting it.

## Area3D
Inherits: CollisionObject3D
A region of 3D space that detects other [CollisionObject3D]s entering or exiting it.

## AreaLight3D
Inherits: Light3D
An area light, such as a neon light tube or a screen.

## Array
A built-in data structure that holds a sequence of elements.

## ArrayMesh
Inherits: Mesh
[Mesh] type that provides utility for constructing a surface from arrays.

## ArrayOccluder3D
Inherits: Occluder3D
3D polygon shape for use with occlusion culling in [OccluderInstance3D].

## AspectRatioContainer
Inherits: Container
A container that preserves the proportions of its child controls.

## AtlasTexture
Inherits: Texture2D
A texture that crops out part of another Texture2D.

## AudioBusLayout
Inherits: Resource
Stores information about the audio buses.

## AudioEffect
Inherits: Resource
Base class for audio effect resources.

## AudioEffectAmplify
Inherits: AudioEffect
Adds a volume manipulation audio effect to an audio bus.

## AudioEffectBandLimitFilter
Inherits: AudioEffectFilter
Adds a band-limit filter to an audio bus.

## AudioEffectBandPassFilter
Inherits: AudioEffectFilter
Adds a band-pass filter to an audio bus.

## AudioEffectCapture
Inherits: AudioEffect
Exposes audio samples from an audio bus in real-time, such that it can be accessed as data.

## AudioEffectChorus
Inherits: AudioEffect
Adds a chorus audio effect to an audio bus. Gives the impression of multiple audio sources.

## AudioEffectCompressor
Inherits: AudioEffect
Adds a downward compressor audio effect to an audio bus. Allows control of the dynamic range via a volume threshold and timing controls.

## AudioEffectDelay
Inherits: AudioEffect
Adds a delay audio effect to an audio bus. Emulates an echo by playing the input audio back after a period of time.

## AudioEffectDistortion
Inherits: AudioEffect
Adds a distortion audio effect to an audio bus. Remaps audio samples using a nonlinear function to achieve a distorted sound.

## AudioEffectEQ
Inherits: AudioEffect
Base class for audio equalizers (EQ). Gives you control over frequencies. Use it to create a custom equalizer if [AudioEffectEQ6], [AudioEffectEQ10], or [AudioEffectEQ21] don't fit your needs.

## AudioEffectEQ10
Inherits: AudioEffectEQ
Adds a 10-band equalizer audio effect to an audio bus. Gives you control over frequencies from 31 Hz to 16000 Hz. Each frequency can be modulated between -60/+24 dB.

## AudioEffectEQ21
Inherits: AudioEffectEQ
Adds a 21-band equalizer audio effect to an audio bus. Gives you control over frequencies from 22 Hz to 22000 Hz. Each frequency can be modulated between -60/+24 dB.

## AudioEffectEQ6
Inherits: AudioEffectEQ
Adds a 6-band equalizer audio effect to an audio bus. Gives you control over frequencies from 32 Hz to 10000 Hz. Each frequency can be modulated between -60/+24 dB.

## AudioEffectFilter
Inherits: AudioEffect
Base class for filters. Use effects that inherit this class instead of using it directly.

## AudioEffectHardLimiter
Inherits: AudioEffect
Adds a limiter audio effect to an audio bus. Prevents audio signals from exceeding a specified volume level.

## AudioEffectHighPassFilter
Inherits: AudioEffectFilter
Adds a high-pass filter to an audio bus.

## AudioEffectHighShelfFilter
Inherits: AudioEffectFilter
Adds a high-shelf filter to an audio bus.

## AudioEffectInstance
Inherits: RefCounted
Manipulates the audio it receives for a given effect.

## AudioEffectLimiter
Inherits: AudioEffect
Adds a soft-clip limiter audio effect to an audio bus.

## AudioEffectLowPassFilter
Inherits: AudioEffectFilter
Adds a low-pass filter to an audio bus.

## AudioEffectLowShelfFilter
Inherits: AudioEffectFilter
Adds a low-shelf filter to an audio bus.

## AudioEffectNotchFilter
Inherits: AudioEffectFilter
Adds a notch filter to an audio bus.

## AudioEffectPanner
Inherits: AudioEffect
Adds a panner audio effect to an audio bus. Pans the sound left or right.

## AudioEffectPhaser
Inherits: AudioEffect
Adds a phaser audio effect to an audio bus. Creates several notch and peak filters that sweep across the spectrum.

## AudioEffectPitchShift
Inherits: AudioEffect
Adds a pitch-shifting audio effect to an audio bus. Raises or lowers the pitch of the input audio.

## AudioEffectRecord
Inherits: AudioEffect
Audio effect used for recording the sound from an audio bus.

## AudioEffectReverb
Inherits: AudioEffect
Adds a reverberation audio effect to an audio bus. Emulates an echo by playing a blurred version of the input audio.

## AudioEffectSpectrumAnalyzer
Inherits: AudioEffect
Creates an [AudioEffectInstance] which performs frequency analysis and exposes results to be accessed in real-time.

## AudioEffectSpectrumAnalyzerInstance
Inherits: AudioEffectInstance
Queryable instance of an [AudioEffectSpectrumAnalyzer].

## AudioEffectStereoEnhance
Inherits: AudioEffect
Adds a stereo manipulation audio effect to an audio bus. Controls gain of the side channels, and widens the stereo image.

## AudioListener2D
Inherits: Node2D
Overrides the location sounds are heard from.

## AudioListener3D
Inherits: Node3D
Overrides the location sounds are heard from.

## AudioSample
Inherits: RefCounted
Base class for audio samples.

## AudioSamplePlayback
Inherits: RefCounted
Meta class for playing back audio samples.

## AudioServer
Inherits: Object
Server interface for low-level audio access.

## AudioStream
Inherits: Resource
Base class for audio streams.

## AudioStreamGenerator
Inherits: AudioStream
An audio stream with utilities for procedural sound generation.

## AudioStreamGeneratorPlayback
Inherits: AudioStreamPlaybackResampled
Plays back audio generated using [AudioStreamGenerator].

## AudioStreamMicrophone
Inherits: AudioStream
Plays real-time audio input data.

## AudioStreamPlayback
Inherits: RefCounted
Meta class for playing back audio.

## AudioStreamPlaybackPolyphonic
Inherits: AudioStreamPlayback
Playback instance for [AudioStreamPolyphonic].

## AudioStreamPlaybackResampled
Inherits: AudioStreamPlayback
Playback class used for resampled [AudioStream]s.

## AudioStreamPlayer
Inherits: Node
A node for audio playback.

## AudioStreamPlayer2D
Inherits: Node2D
Plays positional sound in 2D space.

## AudioStreamPlayer3D
Inherits: Node3D
Plays positional sound in 3D space.

## AudioStreamPolyphonic
Inherits: AudioStream
AudioStream that lets the user play custom streams at any time from code, simultaneously using a single player.

## AudioStreamRandomizer
Inherits: AudioStream
Wraps a pool of audio streams with pitch and volume shifting.

## AudioStreamWAV
Inherits: AudioStream
Stores audio data loaded from WAV files.

## AwaitTweener
Inherits: Tweener
Awaits a specified signal.

## BackBufferCopy
Inherits: Node2D
A node that copies a region of the screen to a buffer for access in shader code.

## BaseButton
Inherits: Control
Abstract base class for GUI buttons.

## BaseMaterial3D
Inherits: Material
Abstract base class for defining the 3D rendering properties of meshes.

## Basis
A 3×3 matrix for representing 3D rotation and scale.

## BitMap
Inherits: Resource
Boolean matrix.

## BlitMaterial
Inherits: Material
A material that processes blit calls to a DrawableTexture.

## Bone2D
Inherits: Node2D
A joint used with [Skeleton2D] to control and animate other nodes.

## BoneAttachment3D
Inherits: Node3D
А node that dynamically copies or overrides the 3D transform of a bone in its parent [Skeleton3D].

## BoneConstraint3D
Inherits: SkeletonModifier3D
A node that may modify Skeleton3D's bone with associating the two bones.

## BoneMap
Inherits: Resource
Describes a mapping of bone names for retargeting [Skeleton3D] into common names defined by a [SkeletonProfile].

## BoneTwistDisperser3D
Inherits: SkeletonModifier3D
A node that propagates and disperses the child bone's twist to the parent bones.

## BoxContainer
Inherits: Container
A container that arranges its child controls horizontally or vertically.

## BoxMesh
Inherits: PrimitiveMesh
Generate an axis-aligned box [PrimitiveMesh].

## BoxOccluder3D
Inherits: Occluder3D
Cuboid shape for use with occlusion culling in [OccluderInstance3D].

## BoxShape3D
Inherits: Shape3D
A 3D box shape used for physics collision.

## Button
Inherits: BaseButton
A themed button that can contain text and an icon.

## ButtonGroup
Inherits: Resource
A group of buttons that doesn't allow more than one button to be pressed at a time.

## CCDIK3D
Inherits: IterateIK3D
Rotation based cyclic coordinate descent inverse kinematics solver.

## CPUParticles2D
Inherits: Node2D
A CPU-based 2D particle emitter.

## CPUParticles3D
Inherits: GeometryInstance3D
A CPU-based 3D particle emitter.

## Callable
A built-in type representing a method or a standalone function.

## CallbackTweener
Inherits: Tweener
Calls the specified method after optional delay.

## Camera2D
Inherits: Node2D
Camera node for 2D scenes.

## Camera3D
Inherits: Node3D
Camera node, displays from a point of view.

## CameraAttributes
Inherits: Resource
Parent class for camera settings.

## CameraAttributesPhysical
Inherits: CameraAttributes
Physically-based camera settings.

## CameraAttributesPractical
Inherits: CameraAttributes
Camera settings in an easy to use format.

## CameraFeed
Inherits: RefCounted
A camera feed gives you access to a single physical camera attached to your device.

## CameraServer
Inherits: Object
Server keeping track of different cameras accessible in Godot.

## CameraTexture
Inherits: Texture2D
Texture provided by a [CameraFeed].

## CanvasGroup
Inherits: Node2D
Merges several 2D nodes into a single draw operation.

## CanvasItem
Inherits: Node
Abstract base class for everything in 2D space.

## CanvasItemMaterial
Inherits: Material
A material for [CanvasItem]s.

## CanvasLayer
Inherits: Node
A node used for independent rendering of objects within a 2D scene.

## CanvasModulate
Inherits: Node2D
A node that applies a color tint to a canvas.

## CanvasTexture
Inherits: Texture2D
Texture with optional normal and specular maps for use in 2D rendering.

## CapsuleMesh
Inherits: PrimitiveMesh
Class representing a capsule-shaped [PrimitiveMesh].

## CapsuleShape2D
Inherits: Shape2D
A 2D capsule shape used for physics collision.

## CapsuleShape3D
Inherits: Shape3D
A 3D capsule shape used for physics collision.

## CenterContainer
Inherits: Container
A container that keeps child controls in its center.

## ChainIK3D
Inherits: IKModifier3D
A [SkeletonModifier3D] to apply inverse kinematics to bone chains containing an arbitrary number of bones.

## CharFXTransform
Inherits: RefCounted
Controls how an individual character will be displayed in a [RichTextEffect].

## CharacterBody2D
Inherits: PhysicsBody2D
A 2D physics body specialized for characters moved by script.

## CharacterBody3D
Inherits: PhysicsBody3D
A 3D physics body specialized for characters moved by script.

## CheckBox
Inherits: Button
A button that represents a binary choice.

## CheckButton
Inherits: Button
A button that represents a binary choice.

## CircleShape2D
Inherits: Shape2D
A 2D circle shape used for physics collision.

## ClassDB
Inherits: Object
A class information repository.

## CodeEdit
Inherits: TextEdit
A multiline text editor designed for editing code.

## CodeHighlighter
Inherits: SyntaxHighlighter
A syntax highlighter intended for code.

## CollisionObject2D
Inherits: Node2D
Abstract base class for 2D physics objects.

## CollisionObject3D
Inherits: Node3D
Abstract base class for 3D physics objects.

## CollisionPolygon2D
Inherits: Node2D
A node that provides a polygon shape to a [CollisionObject2D] parent.

## CollisionPolygon3D
Inherits: Node3D
A node that provides a thickened polygon shape (a prism) to a [CollisionObject3D] parent.

## CollisionShape2D
Inherits: Node2D
A node that provides a [Shape2D] to a [CollisionObject2D] parent.

## CollisionShape3D
Inherits: Node3D
A node that provides a [Shape3D] to a [CollisionObject3D] parent.

## Color
A color represented in RGBA format.

## ColorPalette
Inherits: Resource
A resource class for managing a palette of colors, which can be loaded and saved using [ColorPicker].

## ColorPicker
Inherits: VBoxContainer
A widget that provides an interface for selecting or modifying a color.

## ColorPickerButton
Inherits: Button
A button that brings up a [ColorPicker] when pressed.

## ColorRect
Inherits: Control
A control that displays a solid color rectangle.

## Compositor
Inherits: Resource
Stores attributes used to customize how a Viewport is rendered.

## CompositorEffect
Inherits: Resource
This resource allows for creating a custom rendering effect.

## CompressedCubemap
Inherits: CompressedTextureLayered
An optionally compressed [Cubemap].

## CompressedCubemapArray
Inherits: CompressedTextureLayered
An optionally compressed [CubemapArray].

## CompressedTexture2D
Inherits: Texture2D
Texture with 2 dimensions, optionally compressed.

## CompressedTexture2DArray
Inherits: CompressedTextureLayered
Array of 2-dimensional textures, optionally compressed.

## CompressedTexture3D
Inherits: Texture3D
Texture with 3 dimensions, optionally compressed.

## CompressedTextureLayered
Inherits: TextureLayered
Base class for texture arrays that can optionally be compressed.

## ConcavePolygonShape2D
Inherits: Shape2D
A 2D polyline shape used for physics collision.

## ConcavePolygonShape3D
Inherits: Shape3D
A 3D trimesh shape used for physics collision.

## ConeTwistJoint3D
Inherits: Joint3D
A physics joint that connects two 3D physics bodies in a way that simulates a ball-and-socket joint.

## ConfigFile
Inherits: RefCounted
Helper class to handle INI-style files.

## ConfirmationDialog
Inherits: AcceptDialog
A dialog used for confirmation of actions.

## Container
Inherits: Control
Base class for all GUI containers.

## Control
Inherits: CanvasItem
Base class for all GUI controls. Adapts its position and size based on its parent control.

## ConvertTransformModifier3D
Inherits: BoneConstraint3D
A [SkeletonModifier3D] that apply transform to the bone which converted from reference.

## ConvexPolygonShape2D
Inherits: Shape2D
A 2D convex polygon shape used for physics collision.

## ConvexPolygonShape3D
Inherits: Shape3D
A 3D convex polyhedron shape used for physics collision.

## CopyTransformModifier3D
Inherits: BoneConstraint3D
A [SkeletonModifier3D] that apply transform to the bone which copied from reference.

## Crypto
Inherits: RefCounted
Provides access to advanced cryptographic functionalities.

## CryptoKey
Inherits: Resource
A cryptographic key (RSA or elliptic-curve).

## Cubemap
Inherits: ImageTextureLayered
Six square textures representing the faces of a cube. Commonly used as a skybox.

## CubemapArray
Inherits: ImageTextureLayered
An array of [Cubemap]s, stored together and with a single reference.

## Curve
Inherits: Resource
A mathematical curve.

## Curve2D
Inherits: Resource
Describes a Bézier curve in 2D space.

## Curve3D
Inherits: Resource
Describes a Bézier curve in 3D space.

## CurveTexture
Inherits: Texture2D
A 1D texture where pixel brightness corresponds to points on a curve.

## CurveXYZTexture
Inherits: Texture2D
A 1D texture where the red, green, and blue color channels correspond to points on 3 curves.

## CylinderMesh
Inherits: PrimitiveMesh
Class representing a cylindrical [PrimitiveMesh].

## CylinderShape3D
Inherits: Shape3D
A 3D cylinder shape used for physics collision.

## DPITexture
Inherits: Texture2D
An automatically scalable [Texture2D] based on an SVG image.

## DTLSServer
Inherits: RefCounted
Helper class to implement a DTLS server.

## DampedSpringJoint2D
Inherits: Joint2D
A physics joint that connects two 2D physics bodies with a spring-like force.

## Decal
Inherits: VisualInstance3D
Node that projects a texture onto a [MeshInstance3D].

## Dictionary
A built-in data structure that holds key-value pairs.

## DirAccess
Inherits: RefCounted
Provides methods for managing directories and their content.

## DirectionalLight2D
Inherits: Light2D
Directional 2D light from a distance.

## DirectionalLight3D
Inherits: Light3D
Directional light from a distance, as from the Sun.

## DisplayServer
Inherits: Object
A server interface for low-level window management.

## DrawableTexture2D
Inherits: Texture2D
A 2D texture that supports drawing to itself via Blit calls.

## EditorCommandPalette
Inherits: ConfirmationDialog
Godot editor's command palette.

## EditorContextMenuPlugin
Inherits: RefCounted
Plugin for adding custom context menus in the editor.

## EditorDebuggerPlugin
Inherits: RefCounted
A base class to implement debugger plugins.

## EditorDebuggerSession
Inherits: RefCounted
A class to interact with the editor debugger.

## EditorDock
Inherits: MarginContainer
Dockable container for the editor.

## EditorExportPlatform
Inherits: RefCounted
Identifies a supported export platform, and internally provides the functionality of exporting to that platform.

## EditorExportPlatformAppleEmbedded
Inherits: EditorExportPlatform
Base class for the Apple embedded platform exporters (iOS and visionOS).

## EditorExportPlatformExtension
Inherits: EditorExportPlatform
Base class for custom [EditorExportPlatform] implementations (plugins).

## EditorExportPlatformPC
Inherits: EditorExportPlatform
Base class for the desktop platform exporter (Windows and Linux/BSD).

## EditorExportPlugin
Inherits: RefCounted
A script that is executed when exporting the project.

## EditorExportPreset
Inherits: RefCounted
Export preset configuration.

## EditorFeatureProfile
Inherits: RefCounted
An editor feature profile which can be used to disable specific features.

## EditorFileDialog
Inherits: FileDialog
A modified version of [FileDialog] used by the editor.

## EditorFileSystem
Inherits: Node
Resource filesystem, as the editor sees it.

## EditorFileSystemDirectory
Inherits: Object
A directory for the resource filesystem.

## EditorFileSystemImportFormatSupportQuery
Inherits: RefCounted
Used to query and configure import format support.

## EditorImportPlugin
Inherits: ResourceImporter
Registers a custom resource importer in the editor. Use the class to parse any file and import it as a new resource type.

## EditorInspector
Inherits: ScrollContainer
A control used to edit properties of an object.

## EditorInspectorPlugin
Inherits: RefCounted
Plugin for adding custom property editors on the inspector.

## EditorInterface
Inherits: Object
Godot editor's interface.

## EditorNode3DGizmo
Inherits: Node3DGizmo
Gizmo for editing [Node3D] objects.

## EditorNode3DGizmoPlugin
Inherits: Resource
A class used by the editor to define Node3D gizmo types.

## EditorPaths
Inherits: Object
Editor-only singleton that returns paths to various OS-specific data folders and files.

## EditorPlugin
Inherits: Node
Used by the editor to extend its functionality.

## EditorProperty
Inherits: Container
Custom control for editing properties that can be added to the [EditorInspector].

## EditorResourceConversionPlugin
Inherits: RefCounted
Plugin for adding custom converters from one resource format to another in the editor resource picker context menu; for example, converting a [StandardMaterial3D] to a [ShaderMaterial].

## EditorResourcePicker
Inherits: HBoxContainer
Godot editor's control for selecting [Resource] type properties.

## EditorResourcePreview
Inherits: Node
A node used to generate previews of resources or files.

## EditorResourcePreviewGenerator
Inherits: RefCounted
Custom generator of previews.

## EditorResourceTooltipPlugin
Inherits: RefCounted
A plugin that advanced tooltip for its handled resource type.

## EditorSceneFormatImporter
Inherits: RefCounted
Imports scenes from third-parties' 3D files.

## EditorScenePostImport
Inherits: RefCounted
Post-processes scenes after import.

## EditorScenePostImportPlugin
Inherits: RefCounted
Plugin to control and modifying the process of importing a scene.

## EditorScript
Inherits: RefCounted
Base script that can be used to add extension functions to the editor.

## EditorScriptPicker
Inherits: EditorResourcePicker
Godot editor's control for selecting the script property of a [Node].

## EditorSelection
Inherits: Object
Manages the SceneTree selection in the editor.

## EditorSettings
Inherits: Resource
Object that holds the project-independent editor settings.

## EditorSpinSlider
Inherits: Range
Godot editor's control for editing numeric values.

## EditorSyntaxHighlighter
Inherits: SyntaxHighlighter
Base class for [SyntaxHighlighter] used by the [ScriptEditor].

## EditorToaster
Inherits: HBoxContainer
Manages toast notifications within the editor.

## EditorTranslationParserPlugin
Inherits: RefCounted
Plugin for adding custom parsers to extract strings that are to be translated from custom files (.csv, .json etc.).

## EditorUndoRedoManager
Inherits: Object
Manages undo history of scenes opened in the editor.

## EditorVCSInterface
Inherits: Object
Version Control System (VCS) interface, which reads and writes to the local VCS in use.

## EncodedObjectAsID
Inherits: RefCounted
Holds a reference to an [Object]'s instance ID.

## Engine
Inherits: Object
Provides access to engine properties.

## EngineDebugger
Inherits: Object
Exposes the internal debugger.

## EngineProfiler
Inherits: RefCounted
Base class for creating custom profilers.

## Environment
Inherits: Resource
Resource for environment nodes (like [WorldEnvironment]) that define multiple rendering options.

## Expression
Inherits: RefCounted
A class that stores an expression you can execute.

## ExternalTexture
Inherits: Texture2D
Texture which displays the content of an external buffer.

## FABRIK3D
Inherits: IterateIK3D
Position based forward and backward reaching inverse kinematics solver.

## FileAccess
Inherits: RefCounted
Provides methods for file reading and writing operations.

## FileDialog
Inherits: ConfirmationDialog
A dialog for selecting files or directories in the filesystem.

## FileSystemDock
Inherits: EditorDock
Godot editor's dock for managing files in the project.

## FlowContainer
Inherits: Container
A container that arranges its child controls horizontally or vertically and wraps them around at the borders.

## FogMaterial
Inherits: Material
A material that controls how volumetric fog is rendered, to be assigned to a [FogVolume].

## FogVolume
Inherits: VisualInstance3D
A region that contributes to the default volumetric fog from the world environment.

## FoldableContainer
Inherits: Container
A container that can be expanded/collapsed.

## FoldableGroup
Inherits: Resource
A group of foldable containers that doesn't allow more than one container to be expanded at a time.

## Font
Inherits: Resource
Abstract base class for fonts and font variations.

## FontFile
Inherits: Font
Holds font source data and prerendered glyph cache, imported from a dynamic or a bitmap font.

## FontVariation
Inherits: Font
A variation of a font with additional settings.

## FramebufferCacheRD
Inherits: Object
Framebuffer cache manager for Rendering Device based renderers.

## GDExtension
Inherits: Resource
A native library for GDExtension.

## GDExtensionManager
Inherits: Object
Provides access to GDExtension functionality.

## GPUParticles2D
Inherits: Node2D
A 2D particle emitter.

## GPUParticles3D
Inherits: GeometryInstance3D
A 3D particle emitter.

## GPUParticlesAttractor3D
Inherits: VisualInstance3D
Abstract base class for 3D particle attractors.

## GPUParticlesAttractorBox3D
Inherits: GPUParticlesAttractor3D
A box-shaped attractor that influences particles from [GPUParticles3D] nodes.

## GPUParticlesAttractorSphere3D
Inherits: GPUParticlesAttractor3D
A spheroid-shaped attractor that influences particles from [GPUParticles3D] nodes.

## GPUParticlesAttractorVectorField3D
Inherits: GPUParticlesAttractor3D
A box-shaped attractor with varying directions and strengths defined in it that influences particles from [GPUParticles3D] nodes.

## GPUParticlesCollision3D
Inherits: VisualInstance3D
Abstract base class for 3D particle collision shapes affecting [GPUParticles3D] nodes.

## GPUParticlesCollisionBox3D
Inherits: GPUParticlesCollision3D
A box-shaped 3D particle collision shape affecting [GPUParticles3D] nodes.

## GPUParticlesCollisionHeightField3D
Inherits: GPUParticlesCollision3D
A real-time heightmap-shaped 3D particle collision shape affecting [GPUParticles3D] nodes.

## GPUParticlesCollisionSDF3D
Inherits: GPUParticlesCollision3D
A baked signed distance field 3D particle collision shape affecting [GPUParticles3D] nodes.

## GPUParticlesCollisionSphere3D
Inherits: GPUParticlesCollision3D
A sphere-shaped 3D particle collision shape affecting [GPUParticles3D] nodes.

## Generic6DOFJoint3D
Inherits: Joint3D
A physics joint that allows for complex movement and rotation between two 3D physics bodies.

## Geometry2D
Inherits: Object
Provides methods for some common 2D geometric operations.

## Geometry3D
Inherits: Object
Provides methods for some common 3D geometric operations.

## GeometryInstance3D
Inherits: VisualInstance3D
Base node for geometry-based visual instances.

## GodotInstance
Inherits: Object
Provides access to an embedded Godot instance.

## Gradient
Inherits: Resource
A color transition.

## GradientTexture1D
Inherits: Texture2D
A 1D texture that uses colors obtained from a [Gradient].

## GradientTexture2D
Inherits: Texture2D
A 2D texture that creates a pattern with colors obtained from a [Gradient].

## GraphEdit
Inherits: Control
An editor for graph-like structures, using [GraphNode]s.

## GraphElement
Inherits: Container
A container that represents a basic element that can be placed inside a [GraphEdit] control.

## GraphFrame
Inherits: GraphElement
GraphFrame is a special [GraphElement] that can be used to organize other [GraphElement]s inside a [GraphEdit].

## GraphNode
Inherits: GraphElement
A container with connection ports, representing a node in a [GraphEdit].

## GridContainer
Inherits: Container
A container that arranges its child controls in a grid layout.

## GrooveJoint2D
Inherits: Joint2D
A physics joint that restricts the movement of two 2D physics bodies to a fixed axis.

## HBoxContainer
Inherits: BoxContainer
A container that arranges its child controls horizontally.

## HFlowContainer
Inherits: FlowContainer
A container that arranges its child controls horizontally and wraps them around at the borders.

## HMACContext
Inherits: RefCounted
Used to create an HMAC for a message using a key.

## HScrollBar
Inherits: ScrollBar
A horizontal scrollbar that goes from left (min) to right (max).

## HSeparator
Inherits: Separator
A horizontal line used for separating other controls.

## HSlider
Inherits: Slider
A horizontal slider that goes from left (min) to right (max).

## HSplitContainer
Inherits: SplitContainer
A container that splits two child controls horizontally and provides a grabber for adjusting the split ratio.

## HTTPClient
Inherits: RefCounted
Low-level hyper-text transfer protocol client.

## HTTPRequest
Inherits: Node
A node with the ability to send HTTP(S) requests.

## HashingContext
Inherits: RefCounted
Provides functionality for computing cryptographic hashes chunk by chunk.

## HeightMapShape3D
Inherits: Shape3D
A 3D heightmap shape used for physics collision.

## HingeJoint3D
Inherits: Joint3D
A physics joint that restricts the rotation of a 3D physics body around an axis relative to another physics body.

## IKModifier3D
Inherits: SkeletonModifier3D
A node for inverse kinematics which may modify more than one bone.

## IP
Inherits: Object
Internet protocol (IP) support functions such as DNS resolution.

## Image
Inherits: Resource
Image datatype.

## ImageFormatLoader
Inherits: RefCounted
Base class to add support for specific image formats.

## ImageFormatLoaderExtension
Inherits: ImageFormatLoader
Base class for creating [ImageFormatLoader] extensions (adding support for extra image formats).

## ImageTexture
Inherits: Texture2D
A [Texture2D] based on an [Image].

## ImageTexture3D
Inherits: Texture3D
Texture with 3 dimensions.

## ImageTextureLayered
Inherits: TextureLayered
Base class for texture types which contain the data of multiple [ImageTexture]s. Each image is of the same size and format.

## ImmediateMesh
Inherits: Mesh
Mesh optimized for creating geometry manually.

## ImporterMesh
Inherits: Resource
A [Resource] that contains vertex array-based geometry during the import process.

## ImporterMeshInstance3D
Inherits: Node3D
_(no brief description)_

## Input
Inherits: Object
A singleton for handling inputs.

## InputEvent
Inherits: Resource
Abstract base class for input events.

## InputEventAction
Inherits: InputEvent
An input event type for actions.

## InputEventFromWindow
Inherits: InputEvent
Abstract base class for [Viewport]-based input events.

## InputEventGesture
Inherits: InputEventWithModifiers
Abstract base class for touch gestures.

## InputEventJoypadButton
Inherits: InputEvent
Represents a gamepad button being pressed or released.

## InputEventJoypadMotion
Inherits: InputEvent
Represents axis motions (such as joystick or analog triggers) from a gamepad.

## InputEventKey
Inherits: InputEventWithModifiers
Represents a key on a keyboard being pressed or released.

## InputEventMIDI
Inherits: InputEvent
Represents a MIDI message from a MIDI device, such as a musical keyboard.

## InputEventMagnifyGesture
Inherits: InputEventGesture
Represents a magnifying touch gesture.

## InputEventMouse
Inherits: InputEventWithModifiers
Base input event type for mouse events.

## InputEventMouseButton
Inherits: InputEventMouse
Represents a mouse button being pressed or released.

## InputEventMouseMotion
Inherits: InputEventMouse
Represents a mouse or a pen movement.

## InputEventPanGesture
Inherits: InputEventGesture
Represents a panning touch gesture.

## InputEventScreenDrag
Inherits: InputEventFromWindow
Represents a screen drag event.

## InputEventScreenTouch
Inherits: InputEventFromWindow
Represents a screen touch event.

## InputEventShortcut
Inherits: InputEvent
Represents a triggered keyboard [Shortcut].

## InputEventWithModifiers
Inherits: InputEventFromWindow
Abstract base class for input events affected by modifier keys like [kbd]Shift[/kbd] and [kbd]Alt[/kbd].

## InputMap
Inherits: Object
A singleton that manages all [InputEventAction]s.

## InstancePlaceholder
Inherits: Node
Placeholder for the root [Node] of a [PackedScene].

## IntervalTweener
Inherits: Tweener
Creates an idle interval in a [Tween] animation.

## ItemList
Inherits: Control
A vertical list of selectable items with one or multiple columns.

## IterateIK3D
Inherits: ChainIK3D
A [SkeletonModifier3D] to approach the goal by repeating small rotations.

## JNISingleton
Inherits: Object
Singleton that connects the engine with Android plugins to interface with native Android code.

## JSON
Inherits: Resource
Helper class for creating and parsing JSON data.

## JSONRPC
Inherits: Object
A helper to handle dictionaries which look like JSONRPC documents.

## JacobianIK3D
Inherits: IterateIK3D
Jacobian transpose based inverse kinematics solver.

## JavaClass
Inherits: RefCounted
Represents a class from the Java Native Interface.

## JavaClassWrapper
Inherits: Object
Provides access to the Java Native Interface.

## JavaObject
Inherits: RefCounted
Represents an object from the Java Native Interface.

## JavaScriptBridge
Inherits: Object
Singleton that connects the engine with the browser's JavaScript context in Web export.

## JavaScriptObject
Inherits: RefCounted
A wrapper class for web native JavaScript objects.

## Joint2D
Inherits: Node2D
Abstract base class for all 2D physics joints.

## Joint3D
Inherits: Node3D
Abstract base class for all 3D physics joints.

## JointLimitation3D
Inherits: Resource
A base class of the limitation that interacts with [ChainIK3D].

## JointLimitationCone3D
Inherits: JointLimitation3D
A cone shape limitation that interacts with [ChainIK3D].

## KinematicCollision2D
Inherits: RefCounted
Holds collision data from the movement of a [PhysicsBody2D].

## KinematicCollision3D
Inherits: RefCounted
Holds collision data from the movement of a [PhysicsBody3D].

## Label
Inherits: Control
A control for displaying plain text.

## Label3D
Inherits: GeometryInstance3D
A node for displaying plain text in 3D space.

## LabelSettings
Inherits: Resource
Provides common settings to customize the text in a [Label].

## Light2D
Inherits: Node2D
Casts light in a 2D environment.

## Light3D
Inherits: VisualInstance3D
Provides a base class for different kinds of light nodes.

## LightOccluder2D
Inherits: Node2D
Occludes light cast by a Light2D, casting shadows.

## LightmapGI
Inherits: VisualInstance3D
Computes and stores baked lightmaps for fast global illumination.

## LightmapGIData
Inherits: Resource
Contains baked lightmap and dynamic object probe data for [LightmapGI].

## LightmapProbe
Inherits: Node3D
Represents a single manually placed probe for dynamic object lighting with [LightmapGI].

## Lightmapper
Inherits: RefCounted
Abstract class extended by lightmappers, for use in [LightmapGI].

## LightmapperRD
Inherits: Lightmapper
The built-in GPU-based lightmapper for use with [LightmapGI].

## LimitAngularVelocityModifier3D
Inherits: SkeletonModifier3D
Limit bone rotation angular velocity.

## Line2D
Inherits: Node2D
A 2D polyline that can optionally be textured.

## LineEdit
Inherits: Control
An input field for single-line text.

## LinkButton
Inherits: BaseButton
A button that represents a link.

## Logger
Inherits: RefCounted
Custom logger to receive messages from the internal error/warning stream.

## LookAtModifier3D
Inherits: SkeletonModifier3D
The [LookAtModifier3D] rotates a bone to look at a target.

## MainLoop
Inherits: Object
Abstract base class for the game's main loop.

## MarginContainer
Inherits: Container
A container that keeps a margin around its child controls.

## Marker2D
Inherits: Node2D
Generic 2D position hint for editing.

## Marker3D
Inherits: Node3D
Generic 3D position hint for editing.

## Marshalls
Inherits: Object
Data transformation (marshaling) and encoding helpers.

## Material
Inherits: Resource
Virtual base class for applying visual properties to an object, such as color and roughness.

## MenuBar
Inherits: Control
A horizontal menu bar that creates a menu for each [PopupMenu] child.

## MenuButton
Inherits: Button
A button that brings up a [PopupMenu] when clicked.

## Mesh
Inherits: Resource
A [Resource] that contains vertex array-based geometry.

## MeshConvexDecompositionSettings
Inherits: RefCounted
Parameters to be used with a [Mesh] convex decomposition operation.

## MeshDataTool
Inherits: RefCounted
Helper tool to access and edit [Mesh] data.

## MeshInstance2D
Inherits: Node2D
Node used for displaying a [Mesh] in 2D.

## MeshInstance3D
Inherits: GeometryInstance3D
Node that instances meshes into a scenario.

## MeshLibrary
Inherits: Resource
Library of meshes.

## MeshTexture
Inherits: Texture2D
Simple texture that uses a mesh to draw itself.

## MethodTweener
Inherits: Tweener
Interpolates an abstract value and supplies it to a method called over time.

## MissingNode
Inherits: Node
An internal editor class intended for keeping the data of unrecognized nodes.

## MissingResource
Inherits: Resource
An internal editor class intended for keeping the data of unrecognized resources.

## ModifierBoneTarget3D
Inherits: SkeletonModifier3D
А node that dynamically copies the 3D transform of a bone in its parent [Skeleton3D].

## MovieWriter
Inherits: Object
Abstract class for non-real-time video recording encoders.

## MultiMesh
Inherits: Resource
Provides high-performance drawing of a mesh multiple times using GPU instancing.

## MultiMeshInstance2D
Inherits: Node2D
Node that instances a [MultiMesh] in 2D.

## MultiMeshInstance3D
Inherits: GeometryInstance3D
Node that instances a [MultiMesh].

## MultiplayerAPI
Inherits: RefCounted
High-level multiplayer API interface.

## MultiplayerAPIExtension
Inherits: MultiplayerAPI
Base class used for extending the [MultiplayerAPI].

## MultiplayerPeer
Inherits: PacketPeer
Abstract class for specialized [PacketPeer]s used by the [MultiplayerAPI].

## MultiplayerPeerExtension
Inherits: MultiplayerPeer
Class that can be inherited to implement custom multiplayer API networking layers via GDExtension.

## Mutex
Inherits: RefCounted
A binary [Semaphore] for synchronization of multiple [Thread]s.

## NativeMenu
Inherits: Object
A server interface for OS native menus.

## NavigationAgent2D
Inherits: Node
A 2D agent used to pathfind to a position while avoiding obstacles.

## NavigationAgent3D
Inherits: Node
A 3D agent used to pathfind to a position while avoiding obstacles.

## NavigationLink2D
Inherits: Node2D
A link between two positions on [NavigationRegion2D]s that agents can be routed through.

## NavigationLink3D
Inherits: Node3D
A link between two positions on [NavigationRegion3D]s that agents can be routed through.

## NavigationMesh
Inherits: Resource
A navigation mesh that defines traversable areas and obstacles.

## NavigationMeshGenerator
Inherits: Object
Helper class for creating and clearing navigation meshes.

## NavigationMeshSourceGeometryData2D
Inherits: Resource
Container for parsed source geometry data used in navigation mesh baking.

## NavigationMeshSourceGeometryData3D
Inherits: Resource
Container for parsed source geometry data used in navigation mesh baking.

## NavigationObstacle2D
Inherits: Node2D
2D obstacle used to affect navigation mesh baking or constrain velocities of avoidance controlled agents.

## NavigationObstacle3D
Inherits: Node3D
3D obstacle used to affect navigation mesh baking or constrain velocities of avoidance controlled agents.

## NavigationPathQueryParameters2D
Inherits: RefCounted
Provides parameters for 2D navigation path queries.

## NavigationPathQueryParameters3D
Inherits: RefCounted
Provides parameters for 3D navigation path queries.

## NavigationPathQueryResult2D
Inherits: RefCounted
Represents the result of a 2D pathfinding query.

## NavigationPathQueryResult3D
Inherits: RefCounted
Represents the result of a 3D pathfinding query.

## NavigationPolygon
Inherits: Resource
A 2D navigation mesh that describes a traversable surface for pathfinding.

## NavigationRegion2D
Inherits: Node2D
A traversable 2D region that [NavigationAgent2D]s can use for pathfinding.

## NavigationRegion3D
Inherits: Node3D
A traversable 3D region that [NavigationAgent3D]s can use for pathfinding.

## NavigationServer2D
Inherits: Object
A server interface for low-level 2D navigation access.

## NavigationServer2DManager
Inherits: Object
A singleton for managing [NavigationServer2D] implementations.

## NavigationServer3D
Inherits: Object
A server interface for low-level 3D navigation access.

## NavigationServer3DManager
Inherits: Object
A singleton for managing [NavigationServer3D] implementations.

## NinePatchRect
Inherits: Control
A control that displays a texture by keeping its corners intact, but tiling its edges and center.

## Node
Inherits: Object
Base class for all scene objects.

## Node2D
Inherits: CanvasItem
A 2D game object, inherited by all 2D-related nodes. Has a position, rotation, scale, and skew.

## Node3D
Inherits: Node
Base object in 3D space, inherited by all 3D nodes.

## Node3DGizmo
Inherits: RefCounted
Abstract class to expose editor gizmos for [Node3D].

## NodePath
A pre-parsed scene tree path.

## ORMMaterial3D
Inherits: BaseMaterial3D
A PBR (Physically Based Rendering) material to be used on 3D objects. Uses an ORM texture.

## OS
Inherits: Object
Provides access to common operating system functionalities.

## Object
Base class for all other classes in the engine.

## Occluder3D
Inherits: Resource
Occluder shape resource for use with occlusion culling in [OccluderInstance3D].

## OccluderInstance3D
Inherits: VisualInstance3D
Provides occlusion culling for 3D nodes, which improves performance in closed areas.

## OccluderPolygon2D
Inherits: Resource
Defines a 2D polygon for LightOccluder2D.

## OmniLight3D
Inherits: Light3D
Omnidirectional light, such as a light bulb or a candle.

## OptimizedTranslation
Inherits: Translation
An optimized translation.

## OptionButton
Inherits: Button
A button that brings up a dropdown with selectable options when pressed.

## PCKPacker
Inherits: RefCounted
Creates packages that can be loaded into a running project.

## PackedByteArray
A packed array of bytes.

## PackedColorArray
A packed array of [Color]s.

## PackedDataContainer
Inherits: Resource
Efficiently packs and serializes [Array] or [Dictionary].

## PackedDataContainerRef
Inherits: RefCounted
An internal class used by [PackedDataContainer] to pack nested arrays and dictionaries.

## PackedFloat32Array
A packed array of 32-bit floating-point values.

## PackedFloat64Array
A packed array of 64-bit floating-point values.

## PackedInt32Array
A packed array of 32-bit integers.

## PackedInt64Array
A packed array of 64-bit integers.

## PackedScene
Inherits: Resource
An abstraction of a serialized scene.

## PackedStringArray
A packed array of [String]s.

## PackedVector2Array
A packed array of [Vector2]s.

## PackedVector3Array
A packed array of [Vector3]s.

## PackedVector4Array
A packed array of [Vector4]s.

## PacketPeer
Inherits: RefCounted
Abstraction and base class for packet-based protocols.

## PacketPeerDTLS
Inherits: PacketPeer
DTLS packet peer.

## PacketPeerExtension
Inherits: PacketPeer
_(no brief description)_

## PacketPeerStream
Inherits: PacketPeer
Wrapper to use a PacketPeer over a StreamPeer.

## PacketPeerUDP
Inherits: PacketPeer
UDP packet peer.

## Panel
Inherits: Control
A GUI control that displays a [StyleBox].

## PanelContainer
Inherits: Container
A container that keeps its child controls within the area of a [StyleBox].

## PanoramaSkyMaterial
Inherits: Material
A material that provides a special texture to a [Sky], usually an HDR panorama.

## Parallax2D
Inherits: Node2D
A node used to create a parallax scrolling background.

## ParallaxBackground
Inherits: CanvasLayer
A node used to create a parallax scrolling background.

## ParallaxLayer
Inherits: Node2D
A parallax scrolling layer to be used with [ParallaxBackground].

## ParticleProcessMaterial
Inherits: Material
Holds a particle configuration for [GPUParticles2D] or [GPUParticles3D] nodes.

## Path2D
Inherits: Node2D
Contains a [Curve2D] path for [PathFollow2D] nodes to follow.

## Path3D
Inherits: Node3D
Contains a [Curve3D] path for [PathFollow3D] nodes to follow.

## PathFollow2D
Inherits: Node2D
Point sampler for a [Path2D].

## PathFollow3D
Inherits: Node3D
Point sampler for a [Path3D].

## Performance
Inherits: Object
Exposes performance-related data.

## PhysicalBone2D
Inherits: RigidBody2D
A [RigidBody2D]-derived node used to make [Bone2D]s in a [Skeleton2D] react to physics.

## PhysicalBone3D
Inherits: PhysicsBody3D
A physics body used to make bones in a [Skeleton3D] react to physics.

## PhysicalBoneSimulator3D
Inherits: SkeletonModifier3D
Node that can be the parent of [PhysicalBone3D] and can apply the simulation results to [Skeleton3D].

## PhysicalSkyMaterial
Inherits: Material
A material that defines a sky for a [Sky] resource by a set of physical properties.

## PhysicsBody2D
Inherits: CollisionObject2D
Abstract base class for 2D game objects affected by physics.

## PhysicsBody3D
Inherits: CollisionObject3D
Abstract base class for 3D game objects affected by physics.

## PhysicsDirectBodyState2D
Inherits: Object
Provides direct access to a physics body in the [PhysicsServer2D].

## PhysicsDirectBodyState2DExtension
Inherits: PhysicsDirectBodyState2D
Provides virtual methods that can be overridden to create custom [PhysicsDirectBodyState2D] implementations.

## PhysicsDirectBodyState3D
Inherits: Object
Provides direct access to a physics body in the [PhysicsServer3D].

## PhysicsDirectBodyState3DExtension
Inherits: PhysicsDirectBodyState3D
Provides virtual methods that can be overridden to create custom [PhysicsDirectBodyState3D] implementations.

## PhysicsDirectSpaceState2D
Inherits: Object
Provides direct access to a physics space in the [PhysicsServer2D].

## PhysicsDirectSpaceState2DExtension
Inherits: PhysicsDirectSpaceState2D
Provides virtual methods that can be overridden to create custom [PhysicsDirectSpaceState2D] implementations.

## PhysicsDirectSpaceState3D
Inherits: Object
Provides direct access to a physics space in the [PhysicsServer3D].

## PhysicsDirectSpaceState3DExtension
Inherits: PhysicsDirectSpaceState3D
Provides virtual methods that can be overridden to create custom [PhysicsDirectSpaceState3D] implementations.

## PhysicsMaterial
Inherits: Resource
Holds physics-related properties of a surface, namely its roughness and bounciness.

## PhysicsPointQueryParameters2D
Inherits: RefCounted
Provides parameters for .

## PhysicsPointQueryParameters3D
Inherits: RefCounted
Provides parameters for .

## PhysicsRayQueryParameters2D
Inherits: RefCounted
Provides parameters for .

## PhysicsRayQueryParameters3D
Inherits: RefCounted
Provides parameters for .

## PhysicsServer2D
Inherits: Object
A server interface for low-level 2D physics access.

## PhysicsServer2DExtension
Inherits: PhysicsServer2D
Provides virtual methods that can be overridden to create custom [PhysicsServer2D] implementations.

## PhysicsServer2DManager
Inherits: Object
A singleton for managing [PhysicsServer2D] implementations.

## PhysicsServer3D
Inherits: Object
A server interface for low-level 3D physics access.

## PhysicsServer3DExtension
Inherits: PhysicsServer3D
Provides virtual methods that can be overridden to create custom [PhysicsServer3D] implementations.

## PhysicsServer3DManager
Inherits: Object
A singleton for managing [PhysicsServer3D] implementations.

## PhysicsServer3DRenderingServerHandler
Inherits: Object
A class used to provide with a rendering handler for soft bodies.

## PhysicsShapeQueryParameters2D
Inherits: RefCounted
Provides parameters for [PhysicsDirectSpaceState2D]'s methods.

## PhysicsShapeQueryParameters3D
Inherits: RefCounted
Provides parameters for [PhysicsDirectSpaceState3D]'s methods.

## PhysicsTestMotionParameters2D
Inherits: RefCounted
Provides parameters for .

## PhysicsTestMotionParameters3D
Inherits: RefCounted
Provides parameters for .

## PhysicsTestMotionResult2D
Inherits: RefCounted
Describes the motion and collision result from .

## PhysicsTestMotionResult3D
Inherits: RefCounted
Describes the motion and collision result from .

## PinJoint2D
Inherits: Joint2D
A physics joint that attaches two 2D physics bodies at a single point, allowing them to freely rotate.

## PinJoint3D
Inherits: Joint3D
A physics joint that attaches two 3D physics bodies at a single point, allowing them to freely rotate.

## PlaceholderCubemap
Inherits: PlaceholderTextureLayered
A [Cubemap] without image data.

## PlaceholderCubemapArray
Inherits: PlaceholderTextureLayered
A [CubemapArray] without image data.

## PlaceholderMaterial
Inherits: Material
Placeholder class for a material.

## PlaceholderMesh
Inherits: Mesh
Placeholder class for a mesh.

## PlaceholderTexture2D
Inherits: Texture2D
Placeholder class for a 2-dimensional texture.

## PlaceholderTexture2DArray
Inherits: PlaceholderTextureLayered
Placeholder class for a 2-dimensional texture array.

## PlaceholderTexture3D
Inherits: Texture3D
Placeholder class for a 3-dimensional texture.

## PlaceholderTextureLayered
Inherits: TextureLayered
Placeholder class for a 2-dimensional texture array.

## Plane
A plane in Hessian normal form.

## PlaneMesh
Inherits: PrimitiveMesh
Class representing a planar [PrimitiveMesh].

## PointLight2D
Inherits: Light2D
Positional 2D light source.

## PointMesh
Inherits: PrimitiveMesh
Mesh with a single point primitive.

## Polygon2D
Inherits: Node2D
A 2D polygon.

## PolygonOccluder3D
Inherits: Occluder3D
Flat 2D polygon shape for use with occlusion culling in [OccluderInstance3D].

## PolygonPathFinder
Inherits: Resource
_(no brief description)_

## Popup
Inherits: Window
Base class for contextual windows and panels with fixed position.

## PopupMenu
Inherits: Popup
A modal window used to display a list of options.

## PopupPanel
Inherits: Popup
A popup with a panel background.

## PortableCompressedTexture2D
Inherits: Texture2D
Provides a compressed texture for disk and/or VRAM in a way that is portable.

## PrimitiveMesh
Inherits: Mesh
Base class for all primitive meshes. Handles applying a [Material] to a primitive mesh.

## PrismMesh
Inherits: PrimitiveMesh
Class representing a prism-shaped [PrimitiveMesh].

## ProceduralSkyMaterial
Inherits: Material
A material that defines a simple sky for a [Sky] resource.

## ProgressBar
Inherits: Range
A control used for visual representation of a percentage.

## ProjectSettings
Inherits: Object
Stores globally-accessible variables.

## Projection
A 4×4 matrix for 3D projective transformations.

## PropertyTweener
Inherits: Tweener
Interpolates an [Object]'s property over time.

## QuadMesh
Inherits: PlaneMesh
Class representing a square mesh facing the camera.

## QuadOccluder3D
Inherits: Occluder3D
Flat plane shape for use with occlusion culling in [OccluderInstance3D].

## Quaternion
A unit quaternion used for representing 3D rotations.

## RDAccelerationStructureGeometry
Inherits: RefCounted
Acceleration structure geometry (used by [RenderingDevice]).

## RDAccelerationStructureInstance
Inherits: RefCounted
Acceleration structure instance (used by [RenderingDevice]).

## RDAttachmentFormat
Inherits: RefCounted
Attachment format (used by [RenderingDevice]).

## RDFramebufferPass
Inherits: RefCounted
Framebuffer pass attachment description (used by [RenderingDevice]).

## RDHitGroup
Inherits: RefCounted
Hit group (used by [RenderingDevice]).

## RDPipelineColorBlendState
Inherits: RefCounted
Pipeline color blend state (used by [RenderingDevice]).

## RDPipelineColorBlendStateAttachment
Inherits: RefCounted
Pipeline color blend state attachment (used by [RenderingDevice]).

## RDPipelineDepthStencilState
Inherits: RefCounted
Pipeline depth/stencil state (used by [RenderingDevice]).

## RDPipelineMultisampleState
Inherits: RefCounted
Pipeline multisample state (used by [RenderingDevice]).

## RDPipelineRasterizationState
Inherits: RefCounted
Pipeline rasterization state (used by [RenderingDevice]).

## RDPipelineShader
Inherits: RefCounted
Pipeline shader (used by [RenderingDevice]).

## RDPipelineSpecializationConstant
Inherits: RefCounted
Pipeline specialization constant (used by [RenderingDevice]).

## RDSamplerState
Inherits: RefCounted
Sampler state (used by [RenderingDevice]).

## RDShaderFile
Inherits: Resource
Compiled shader file in SPIR-V form (used by [RenderingDevice]). Not to be confused with Godot's own [Shader].

## RDShaderSPIRV
Inherits: Resource
SPIR-V intermediate representation as part of an [RDShaderFile] (used by [RenderingDevice]).

## RDShaderSource
Inherits: RefCounted
Shader source code (used by [RenderingDevice]).

## RDTextureFormat
Inherits: RefCounted
Texture format (used by [RenderingDevice]).

## RDTextureView
Inherits: RefCounted
Texture view (used by [RenderingDevice]).

## RDUniform
Inherits: RefCounted
Shader uniform (used by [RenderingDevice]).

## RDVertexAttribute
Inherits: RefCounted
Vertex attribute (used by [RenderingDevice]).

## RID
A handle for a [Resource]'s unique identifier.

## RandomNumberGenerator
Inherits: RefCounted
Provides methods for generating pseudo-random numbers.

## Range
Inherits: Control
Abstract base class for controls that represent a number within a range.

## RayCast2D
Inherits: Node2D
A ray in 2D space, used to find the first collision object it intersects.

## RayCast3D
Inherits: Node3D
A ray in 3D space, used to find the first collision object it intersects.

## Rect2
A 2D axis-aligned bounding box using floating-point coordinates.

## Rect2i
A 2D axis-aligned bounding box using integer coordinates.

## RectangleShape2D
Inherits: Shape2D
A 2D rectangle shape used for physics collision.

## RefCounted
Inherits: Object
Base class for reference-counted objects.

## ReferenceRect
Inherits: Control
A rectangular box for designing UIs.

## ReflectionProbe
Inherits: VisualInstance3D
Captures its surroundings to create fast, accurate reflections from a given point.

## RemoteTransform2D
Inherits: Node2D
RemoteTransform2D pushes its own [Transform2D] to another [Node2D] derived node in the scene.

## RemoteTransform3D
Inherits: Node3D
RemoteTransform3D pushes its own [Transform3D] to another [Node3D] derived Node in the scene.

## RenderData
Inherits: Object
Abstract render data object, holds frame data related to rendering a single frame of a viewport.

## RenderDataExtension
Inherits: RenderData
This class allows for a RenderData implementation to be made in GDExtension.

## RenderDataRD
Inherits: RenderData
Render data implementation for the RenderingDevice based renderers.

## RenderSceneBuffers
Inherits: RefCounted
Abstract scene buffers object, created for each viewport for which 3D rendering is done.

## RenderSceneBuffersConfiguration
Inherits: RefCounted
Configuration object used to setup a [RenderSceneBuffers] object.

## RenderSceneBuffersExtension
Inherits: RenderSceneBuffers
This class allows for a RenderSceneBuffer implementation to be made in GDExtension.

## RenderSceneBuffersRD
Inherits: RenderSceneBuffers
Render scene buffer implementation for the RenderingDevice based renderers.

## RenderSceneData
Inherits: Object
Abstract render data object, holds scene data related to rendering a single frame of a viewport.

## RenderSceneDataExtension
Inherits: RenderSceneData
This class allows for a RenderSceneData implementation to be made in GDExtension.

## RenderSceneDataRD
Inherits: RenderSceneData
Render scene data implementation for the RenderingDevice based renderers.

## RenderingDevice
Inherits: Object
Abstraction for working with modern low-level graphics APIs.

## RenderingServer
Inherits: Object
Server for anything visible.

## Resource
Inherits: RefCounted
Base class for serializable objects.

## ResourceFormatLoader
Inherits: RefCounted
Loads a specific resource type from a file.

## ResourceFormatSaver
Inherits: RefCounted
Saves a specific resource type to a file.

## ResourceImporter
Inherits: RefCounted
Base class for resource importers.

## ResourceImporterBMFont
Inherits: ResourceImporter
Imports a bitmap font in the BMFont (.fnt) format.

## ResourceImporterBitMap
Inherits: ResourceImporter
Imports a [BitMap] resource (2D array of boolean values).

## ResourceImporterCSVTranslation
Inherits: ResourceImporter
Imports comma-separated values as [Translation]s.

## ResourceImporterDynamicFont
Inherits: ResourceImporter
Imports a TTF, TTC, OTF, OTC, WOFF or WOFF2 font file for font rendering that adapts to any size.

## ResourceImporterImage
Inherits: ResourceImporter
Imports an image for use in scripting, with no rendering capabilities.

## ResourceImporterImageFont
Inherits: ResourceImporter
Imports a bitmap font where all glyphs have the same width and height.

## ResourceImporterLayeredTexture
Inherits: ResourceImporter
Imports a 3-dimensional texture ([Texture3D]), a [Texture2DArray], a [Cubemap] or a [CubemapArray].

## ResourceImporterOBJ
Inherits: ResourceImporter
Imports an OBJ 3D model as an independent [Mesh] or scene.

## ResourceImporterSVG
Inherits: ResourceImporter
Imports an SVG file as an automatically scalable texture for use in UI elements and 2D rendering.

## ResourceImporterScene
Inherits: ResourceImporter
Imports a glTF, FBX, COLLADA, or Blender 3D scene.

## ResourceImporterShaderFile
Inherits: ResourceImporter
Imports native GLSL shaders (not Godot shaders) as an [RDShaderFile].

## ResourceImporterTexture
Inherits: ResourceImporter
Imports an image for use in 2D or 3D rendering.

## ResourceImporterTextureAtlas
Inherits: ResourceImporter
Imports a collection of textures from a PNG image into an optimized [AtlasTexture] for 2D rendering.

## ResourceImporterWAV
Inherits: ResourceImporter
Imports a WAV audio file for playback.

## ResourceLoader
Inherits: Object
A singleton for loading resource files.

## ResourcePreloader
Inherits: Node
A node used to preload sub-resources inside a scene.

## ResourceSaver
Inherits: Object
A singleton for saving [Resource]s to the filesystem.

## ResourceUID
Inherits: Object
A singleton that manages the unique identifiers of all resources within a project.

## RetargetModifier3D
Inherits: SkeletonModifier3D
A modifier to transfer parent skeleton poses (or global poses) to child skeletons in model space with different rests.

## RibbonTrailMesh
Inherits: PrimitiveMesh
Represents a straight ribbon-shaped [PrimitiveMesh] with variable width.

## RichTextEffect
Inherits: Resource
A custom effect for a [RichTextLabel].

## RichTextLabel
Inherits: Control
A control for displaying text that can contain different font styles, images, and basic formatting.

## RigidBody2D
Inherits: PhysicsBody2D
A 2D physics body that is moved by a physics simulation.

## RigidBody3D
Inherits: PhysicsBody3D
A 3D physics body that is moved by a physics simulation.

## RootMotionView
Inherits: VisualInstance3D
Editor-only helper for setting up root motion in [AnimationMixer].

## SceneState
Inherits: RefCounted
Provides access to a scene file's information.

## SceneTree
Inherits: MainLoop
Manages the game loop via a hierarchy of nodes.

## SceneTreeTimer
Inherits: RefCounted
One-shot timer.

## Script
Inherits: Resource
A class stored as a resource.

## ScriptBacktrace
Inherits: RefCounted
A captured backtrace of a specific script language.

## ScriptCreateDialog
Inherits: ConfirmationDialog
Godot editor's popup dialog for creating new [Script] files.

## ScriptEditor
Inherits: PanelContainer
Godot editor's script editor.

## ScriptEditorBase
Inherits: VBoxContainer
Base editor for editing scripts in the [ScriptEditor].

## ScriptExtension
Inherits: Script
_(no brief description)_

## ScriptLanguage
Inherits: Object
_(no brief description)_

## ScriptLanguageExtension
Inherits: ScriptLanguage
_(no brief description)_

## ScrollBar
Inherits: Range
Abstract base class for scrollbars.

## ScrollContainer
Inherits: Container
A container used to provide scrollbars to a child control when needed.

## SegmentShape2D
Inherits: Shape2D
A 2D line segment shape used for physics collision.

## Semaphore
Inherits: RefCounted
A synchronization mechanism used to control access to a shared resource by [Thread]s.

## SeparationRayShape2D
Inherits: Shape2D
A 2D ray shape used for physics collision that tries to separate itself from any collider.

## SeparationRayShape3D
Inherits: Shape3D
A 3D ray shape used for physics collision that tries to separate itself from any collider.

## Separator
Inherits: Control
Abstract base class for separators.

## Shader
Inherits: Resource
A shader implemented in the Godot shading language.

## ShaderGlobalsOverride
Inherits: Node
A node used to override global shader parameters' values in a scene.

## ShaderInclude
Inherits: Resource
A snippet of shader code to be included in a [Shader] with #include.

## ShaderIncludeDB
Inherits: Object
Internal database of built in shader include files.

## ShaderMaterial
Inherits: Material
A material defined by a custom [Shader] program and the values of its shader parameters.

## Shape2D
Inherits: Resource
Abstract base class for 2D shapes used for physics collision.

## Shape3D
Inherits: Resource
Abstract base class for 3D shapes used for physics collision.

## ShapeCast2D
Inherits: Node2D
A 2D shape that sweeps a region of space to detect [CollisionObject2D]s.

## ShapeCast3D
Inherits: Node3D
A 3D shape that sweeps a region of space to detect [CollisionObject3D]s.

## Shortcut
Inherits: Resource
A shortcut for binding input.

## Signal
A built-in type representing a signal of an [Object].

## Skeleton2D
Inherits: Node2D
The parent of a hierarchy of [Bone2D]s, used to create a 2D skeletal animation.

## Skeleton3D
Inherits: Node3D
A node containing a bone hierarchy, used to create a 3D skeletal animation.

## SkeletonIK3D
Inherits: SkeletonModifier3D
A node used to rotate all bones of a [Skeleton3D] bone chain a way that places the end bone at a desired 3D position.

## SkeletonModification2D
Inherits: Resource
Base class for resources that operate on [Bone2D]s in a [Skeleton2D].

## SkeletonModification2DCCDIK
Inherits: SkeletonModification2D
A modification that uses CCDIK to manipulate a series of bones to reach a target in 2D.

## SkeletonModification2DFABRIK
Inherits: SkeletonModification2D
A modification that uses FABRIK to manipulate a series of [Bone2D] nodes to reach a target.

## SkeletonModification2DJiggle
Inherits: SkeletonModification2D
A modification that jiggles [Bone2D] nodes as they move towards a target.

## SkeletonModification2DLookAt
Inherits: SkeletonModification2D
A modification that rotates a [Bone2D] node to look at a target.

## SkeletonModification2DPhysicalBones
Inherits: SkeletonModification2D
A modification that applies the transforms of [PhysicalBone2D] nodes to [Bone2D] nodes.

## SkeletonModification2DStackHolder
Inherits: SkeletonModification2D
A modification that holds and executes a [SkeletonModificationStack2D].

## SkeletonModification2DTwoBoneIK
Inherits: SkeletonModification2D
A modification that rotates two bones using the law of cosines to reach the target.

## SkeletonModificationStack2D
Inherits: Resource
A resource that holds a stack of [SkeletonModification2D]s.

## SkeletonModifier3D
Inherits: Node3D
A node that may modify a Skeleton3D's bones.

## SkeletonProfile
Inherits: Resource
Base class for a profile of a virtual skeleton used as a target for retargeting.

## SkeletonProfileHumanoid
Inherits: SkeletonProfile
A humanoid [SkeletonProfile] preset.

## Skin
Inherits: Resource
_(no brief description)_

## SkinReference
Inherits: RefCounted
A reference-counted holder object for a skeleton RID used in the [RenderingServer].

## Sky
Inherits: Resource
Defines a 3D environment's background by using a [Material].

## Slider
Inherits: Range
Abstract base class for sliders.

## SliderJoint3D
Inherits: Joint3D
A physics joint that restricts the movement of a 3D physics body along an axis relative to another physics body.

## SocketServer
Inherits: RefCounted
An abstract class for servers based on sockets.

## SoftBody3D
Inherits: MeshInstance3D
A deformable 3D physics mesh.

## SphereMesh
Inherits: PrimitiveMesh
Class representing a spherical [PrimitiveMesh].

## SphereOccluder3D
Inherits: Occluder3D
Spherical shape for use with occlusion culling in [OccluderInstance3D].

## SphereShape3D
Inherits: Shape3D
A 3D sphere shape used for physics collision.

## SpinBox
Inherits: Range
An input field for numbers.

## SplineIK3D
Inherits: ChainIK3D
A [SkeletonModifier3D] for aligning bones along a [Path3D].

## SplitContainer
Inherits: Container
A container that arranges child controls horizontally or vertically and provides grabbers for adjusting the split ratios between them.

## SpotLight3D
Inherits: Light3D
A spotlight, such as a reflector spotlight or a lantern.

## SpringArm3D
Inherits: Node3D
A 3D raycast that dynamically moves its children near the collision point.

## SpringBoneCollision3D
Inherits: Node3D
A base class of the collision that interacts with [SpringBoneSimulator3D].

## SpringBoneCollisionCapsule3D
Inherits: SpringBoneCollision3D
A capsule shape collision that interacts with [SpringBoneSimulator3D].

## SpringBoneCollisionPlane3D
Inherits: SpringBoneCollision3D
An infinite plane collision that interacts with [SpringBoneSimulator3D].

## SpringBoneCollisionSphere3D
Inherits: SpringBoneCollision3D
A sphere shape collision that interacts with [SpringBoneSimulator3D].

## SpringBoneSimulator3D
Inherits: SkeletonModifier3D
A [SkeletonModifier3D] to apply inertial wavering to bone chains.

## Sprite2D
Inherits: Node2D
General-purpose sprite node.

## Sprite3D
Inherits: SpriteBase3D
2D sprite node in a 3D world.

## SpriteBase3D
Inherits: GeometryInstance3D
2D sprite node in 3D environment.

## SpriteFrames
Inherits: Resource
Sprite frame library for AnimatedSprite2D and AnimatedSprite3D.

## StandardMaterial3D
Inherits: BaseMaterial3D
A PBR (Physically Based Rendering) material to be used on 3D objects.

## StaticBody2D
Inherits: PhysicsBody2D
A 2D physics body that can't be moved by external forces. When moved manually, it doesn't affect other bodies in its path.

## StaticBody3D
Inherits: PhysicsBody3D
A 3D physics body that can't be moved by external forces. When moved manually, it doesn't affect other bodies in its path.

## StatusIndicator
Inherits: Node
Application status indicator (aka notification area icon). Note: Status indicator is implemented on macOS and Windows.

## StreamPeer
Inherits: RefCounted
Abstract base class for interacting with streams.

## StreamPeerBuffer
Inherits: StreamPeer
A stream peer used to handle binary data streams.

## StreamPeerExtension
Inherits: StreamPeer
_(no brief description)_

## StreamPeerGZIP
Inherits: StreamPeer
A stream peer that handles GZIP and deflate compression/decompression.

## StreamPeerSocket
Inherits: StreamPeer
Abstract base class for interacting with socket streams.

## StreamPeerTCP
Inherits: StreamPeerSocket
A stream peer that handles TCP connections.

## StreamPeerTLS
Inherits: StreamPeer
A stream peer that handles TLS connections.

## StreamPeerUDS
Inherits: StreamPeerSocket
A stream peer that handles UNIX Domain Socket (UDS) connections.

## String
A built-in type for strings.

## StringName
A built-in type for unique strings.

## StyleBox
Inherits: Resource
Abstract base class for defining stylized boxes for UI elements.

## StyleBoxEmpty
Inherits: StyleBox
An empty [StyleBox] (does not display anything).

## StyleBoxFlat
Inherits: StyleBox
A customizable [StyleBox] that doesn't use a texture.

## StyleBoxLine
Inherits: StyleBox
A [StyleBox] that displays a single line of a given color and thickness.

## StyleBoxTexture
Inherits: StyleBox
A texture-based nine-patch [StyleBox].

## SubViewport
Inherits: Viewport
An interface to a game world that doesn't create a window or draw to the screen directly.

## SubViewportContainer
Inherits: Container
A container used for displaying the contents of a [SubViewport].

## SubtweenTweener
Inherits: Tweener
Runs a [Tween] nested within another [Tween].

## SurfaceTool
Inherits: RefCounted
Helper tool to create geometry.

## SyntaxHighlighter
Inherits: Resource
Base class for syntax highlighters. Provides syntax highlighting data to a [TextEdit].

## SystemFont
Inherits: Font
A font loaded from a system font. Falls back to a default theme font if not implemented on the host OS.

## TCPServer
Inherits: SocketServer
A TCP server.

## TLSOptions
Inherits: RefCounted
TLS configuration for clients and servers.

## TabBar
Inherits: Control
A control that provides a horizontal bar with tabs.

## TabContainer
Inherits: Container
A container that creates a tab for each child control, displaying only the active tab's control.

## TextEdit
Inherits: Control
A multiline text editor.

## TextLine
Inherits: RefCounted
Holds a line of text.

## TextMesh
Inherits: PrimitiveMesh
Generate a [PrimitiveMesh] from the text.

## TextParagraph
Inherits: RefCounted
Holds a paragraph of text.

## TextServer
Inherits: RefCounted
A server interface for font management and text rendering.

## TextServerDummy
Inherits: TextServerExtension
A dummy text server that can't render text or manage fonts.

## TextServerExtension
Inherits: TextServer
Base class for custom [TextServer] implementations (plugins).

## TextServerManager
Inherits: Object
A singleton for managing [TextServer] implementations.

## Texture
Inherits: Resource
Base class for all texture types.

## Texture2D
Inherits: Texture
Texture for 2D and 3D.

## Texture2DArray
Inherits: ImageTextureLayered
A single texture resource which consists of multiple, separate images. Each image has the same dimensions and number of mipmap levels.

## Texture2DArrayRD
Inherits: TextureLayeredRD
Texture Array for 2D that is bound to a texture created on the [RenderingDevice].

## Texture2DRD
Inherits: Texture2D
Texture for 2D that is bound to a texture created on the [RenderingDevice].

## Texture3D
Inherits: Texture
Base class for 3-dimensional textures.

## Texture3DRD
Inherits: Texture3D
Texture for 3D that is bound to a texture created on the [RenderingDevice].

## TextureButton
Inherits: BaseButton
Texture-based button. Supports Pressed, Hover, Disabled and Focused states.

## TextureCubemapArrayRD
Inherits: TextureLayeredRD
Texture Array for Cubemaps that is bound to a texture created on the [RenderingDevice].

## TextureCubemapRD
Inherits: TextureLayeredRD
Texture for Cubemap that is bound to a texture created on the [RenderingDevice].

## TextureLayered
Inherits: Texture
Base class for texture types which contain the data of multiple [Image]s. Each image is of the same size and format.

## TextureLayeredRD
Inherits: TextureLayered
Abstract base class for layered texture RD types.

## TextureProgressBar
Inherits: Range
Texture-based progress bar. Useful for loading screens and life or stamina bars.

## TextureRect
Inherits: Control
A control that displays a texture.

## Theme
Inherits: Resource
A resource used for styling/skinning [Control]s and [Window]s.

## ThemeDB
Inherits: Object
A singleton that provides access to static information about [Theme] resources used by the engine and by your project.

## Thread
Inherits: RefCounted
A unit of execution in a process.

## TileData
Inherits: Object
Settings for a single tile in a [TileSet].

## TileMap
Inherits: Node2D
Node for 2D tile-based maps.

## TileMapLayer
Inherits: Node2D
Node for 2D tile-based maps.

## TileMapPattern
Inherits: Resource
Holds a pattern to be copied from or pasted into [TileMap]s.

## TileSet
Inherits: Resource
Tile library for tilemaps.

## TileSetAtlasSource
Inherits: TileSetSource
Exposes a 2D atlas texture as a set of tiles for a [TileSet] resource.

## TileSetScenesCollectionSource
Inherits: TileSetSource
Exposes a set of scenes as tiles for a [TileSet] resource.

## TileSetSource
Inherits: Resource
Exposes a set of tiles for a [TileSet] resource.

## Time
Inherits: Object
A singleton for working with time data.

## Timer
Inherits: Node
A countdown timer.

## TorusMesh
Inherits: PrimitiveMesh
Class representing a torus [PrimitiveMesh].

## TouchScreenButton
Inherits: Node2D
Button for touch screen devices for gameplay use.

## Transform2D
A 2×3 matrix representing a 2D transformation.

## Transform3D
A 3×4 matrix representing a 3D transformation.

## Translation
Inherits: Resource
A language translation that maps a collection of strings to their individual translations.

## TranslationDomain
Inherits: RefCounted
A self-contained collection of [Translation] resources.

## TranslationServer
Inherits: Object
The server responsible for language translations.

## Tree
Inherits: Control
A control used to show a set of internal [TreeItem]s in a hierarchical structure.

## TreeItem
Inherits: Object
An internal control for a single item inside [Tree].

## TriangleMesh
Inherits: RefCounted
Triangle geometry for efficient, physicsless intersection queries.

## TubeTrailMesh
Inherits: PrimitiveMesh
Represents a straight tube-shaped [PrimitiveMesh] with variable width.

## Tween
Inherits: RefCounted
Lightweight object used for general-purpose animation via script, using [Tweener]s.

## Tweener
Inherits: RefCounted
Abstract class for all Tweeners used by [Tween].

## TwoBoneIK3D
Inherits: IKModifier3D
Rotation based intersection of two circles inverse kinematics solver.

## UDPServer
Inherits: RefCounted
Helper class to implement a UDP server.

## UDSServer
Inherits: SocketServer
A Unix Domain Socket (UDS) server.

## UndoRedo
Inherits: Object
Provides a high-level interface for implementing undo and redo operations.

## UniformSetCacheRD
Inherits: Object
Uniform set cache manager for Rendering Device based renderers.

## VBoxContainer
Inherits: BoxContainer
A container that arranges its child controls vertically.

## VFlowContainer
Inherits: FlowContainer
A container that arranges its child controls vertically and wraps them around at the borders.

## VScrollBar
Inherits: ScrollBar
A vertical scrollbar that goes from top (min) to bottom (max).

## VSeparator
Inherits: Separator
A vertical line used for separating other controls.

## VSlider
Inherits: Slider
A vertical slider that goes from bottom (min) to top (max).

## VSplitContainer
Inherits: SplitContainer
A container that splits two child controls vertically and provides a grabber for adjusting the split ratio.

## Variant
The most important data type in Godot.

## Vector2
A 2D vector using floating-point coordinates.

## Vector2i
A 2D vector using integer coordinates.

## Vector3
A 3D vector using floating-point coordinates.

## Vector3i
A 3D vector using integer coordinates.

## Vector4
A 4D vector using floating-point coordinates.

## Vector4i
A 4D vector using integer coordinates.

## VehicleBody3D
Inherits: RigidBody3D
A 3D physics body that simulates the behavior of a car.

## VehicleWheel3D
Inherits: Node3D
A 3D physics body for a [VehicleBody3D] that simulates the behavior of a wheel.

## VideoStream
Inherits: Resource
Base resource for video streams.

## VideoStreamPlayback
Inherits: Resource
Internal class used by [VideoStream] to manage playback state when played from a [VideoStreamPlayer].

## VideoStreamPlayer
Inherits: Control
A control used for video playback.

## Viewport
Inherits: Node
Abstract base class for viewports. Encapsulates drawing and interaction with a game world.

## ViewportTexture
Inherits: Texture2D
Provides the content of a [Viewport] as a dynamic texture.

## VirtualJoystick
Inherits: Control
A virtual joystick control for touchscreen devices.

## VisibleOnScreenEnabler2D
Inherits: VisibleOnScreenNotifier2D
A rectangular region of 2D space that, when visible on screen, enables a target node.

## VisibleOnScreenEnabler3D
Inherits: VisibleOnScreenNotifier3D
A box-shaped region of 3D space that, when visible on screen, enables a target node.

## VisibleOnScreenNotifier2D
Inherits: Node2D
A rectangular region of 2D space that detects whether it is visible on screen.

## VisibleOnScreenNotifier3D
Inherits: VisualInstance3D
A box-shaped region of 3D space that detects whether it is visible on screen.

## VisualInstance3D
Inherits: Node3D
Parent of all visual 3D nodes.

## VoxelGI
Inherits: VisualInstance3D
Real-time global illumination (GI) probe.

## VoxelGIData
Inherits: Resource
Contains baked voxel global illumination data for use in a [VoxelGI] node.

## WeakRef
Inherits: RefCounted
Holds an [Object]. If the object is [RefCounted], it doesn't update the reference count.

## Window
Inherits: Viewport
Base class for all windows, dialogs, and popups.

## WorkerThreadPool
Inherits: Object
A singleton that allocates some [Thread]s on startup, used to offload tasks to these threads.

## World2D
Inherits: Resource
A resource that holds all components of a 2D world, such as a canvas and a physics space.

## World3D
Inherits: Resource
A resource that holds all components of a 3D world, such as a visual scenario and a physics space.

## WorldBoundaryShape2D
Inherits: Shape2D
A 2D world boundary (half-plane) shape used for physics collision.

## WorldBoundaryShape3D
Inherits: Shape3D
A 3D world boundary (half-space) shape used for physics collision.

## WorldEnvironment
Inherits: Node
Default environment properties for the entire scene (post-processing effects, lighting and background settings).

## X509Certificate
Inherits: Resource
An X509 certificate (e.g. for TLS).

## XMLParser
Inherits: RefCounted
Provides a low-level interface for creating parsers for XML files.

## XRAnchor3D
Inherits: XRNode3D
An anchor point in AR space.

## XRBodyModifier3D
Inherits: SkeletonModifier3D
A node for driving body meshes from [XRBodyTracker] data.

## XRBodyTracker
Inherits: XRPositionalTracker
A tracked body in XR.

## XRCamera3D
Inherits: Camera3D
A camera node which automatically positions itself based on XR tracking data.

## XRController3D
Inherits: XRNode3D
A 3D node representing a spatially-tracked controller.

## XRControllerTracker
Inherits: XRPositionalTracker
A tracked controller.

## XRFaceModifier3D
Inherits: Node3D
A node for driving standard face meshes from [XRFaceTracker] weights.

## XRFaceTracker
Inherits: XRTracker
A tracked face.

## XRHandModifier3D
Inherits: SkeletonModifier3D
A node for driving hand meshes from [XRHandTracker] data.

## XRHandTracker
Inherits: XRPositionalTracker
A tracked hand in XR.

## XRInterface
Inherits: RefCounted
Base class for an XR interface implementation.

## XRInterfaceExtension
Inherits: XRInterface
Base class for XR interface extensions (plugins).

## XRNode3D
Inherits: Node3D
A 3D node that has its position automatically updated by the [XRServer].

## XROrigin3D
Inherits: Node3D
The origin point in AR/VR.

## XRPose
Inherits: RefCounted
This object contains all data related to a pose on a tracked object.

## XRPositionalTracker
Inherits: XRTracker
A tracked object.

## XRServer
Inherits: Object
Server for AR and VR features.

## XRTracker
Inherits: RefCounted
A tracked object.

## XRVRS
Inherits: Object
Helper class for XR interfaces that generates VRS images.

## bool
A built-in boolean type.

## float
A built-in type for floating-point numbers.

## int
A built-in type for integers.
