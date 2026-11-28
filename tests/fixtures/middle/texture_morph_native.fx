// Independent native semantics/sampling probe on a real PMX-derived model.
#define CHANNEL 0
float4x4 WVP : WORLDVIEWPROJECTION;
float4 TM : MULTIPLYINGTEXTURE;
float4 TA : ADDINGTEXTURE;
float4 SM : MULTIPLYINGSPHERETEXTURE;
float4 SA : ADDINGSPHERETEXTURE;
float4 ToonColor : TOONCOLOR;
texture Base : MATERIALTEXTURE;
texture Sphere : MATERIALSPHEREMAP;
texture Toon : MATERIALTOONTEXTURE;
sampler BS = sampler_state { Texture=<Base>; MinFilter=POINT; MagFilter=POINT; AddressU=CLAMP; AddressV=CLAMP; };
sampler SS = sampler_state { Texture=<Sphere>; MinFilter=POINT; MagFilter=POINT; AddressU=CLAMP; AddressV=CLAMP; };
sampler TS = sampler_state { Texture=<Toon>; MinFilter=POINT; MagFilter=POINT; AddressU=CLAMP; AddressV=CLAMP; };
float4 VS(float4 p:POSITION):POSITION { return mul(p,WVP); }
float4 PS():COLOR {
#if CHANNEL == 0
 return float4(TM.rgb*0.5,1);
#elif CHANNEL == 1
 return float4(TA.rgb*0.5+0.25,1);
#elif CHANNEL == 2
 return float4(SM.rgb*0.5,1);
#elif CHANNEL == 3
 return float4(SA.rgb*0.5+0.25,1);
#elif CHANNEL == 4
 return float4(TM.a*0.5,TA.a*0.5+0.25,SM.a*0.5,1);
#elif CHANNEL == 5
 return float4(SA.a*0.5+0.25,tex2D(BS,float2(0.25,0.25)).a,tex2D(SS,float2(0.25,0.25)).a,1);
#elif CHANNEL == 6
 return float4(tex2D(BS,float2(0.25,0.25)).rgb,1);
#elif CHANNEL == 7
 return float4(tex2D(SS,float2(0.25,0.25)).rgb,1);
#elif CHANNEL == 8
 return float4(tex2D(TS,float2(0.25,0.25)).rgb,1);
#elif CHANNEL == 9
 return float4(ToonColor.rgb,1);
#else
 return float4(tex2D(TS,float2(0.25,0.25)).a,ToonColor.a,0,1);
#endif
}
technique Model < string MMDPass="object"; > {
 pass Main { VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 PS(); }
}
technique ModelShadow < string MMDPass="object_ss"; > {
 pass Main { VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 PS(); }
}
